"""Tests for the keeper CLI wiring (keeper_cli.py).

The live client needs web3 + a node, so main() takes an injectable
``client_factory``; here we drive the whole CLI path (config assembly, preview
default, single tick, reporting) with a fake client and no network.
"""

import json
from pathlib import Path

import pytest

from sandick.keeper_bot import KeeperReport, LiquidityResult, RebalanceResult
from sandick.keeper_cli import (
    build_bot,
    format_report,
    load_keeper_inputs,
    main,
)

BASKET = Path("config/sandick.basket.json")


def _write_deploy(tmp_path, asset_ids):
    p = tmp_path / "deploy.json"
    p.write_text(json.dumps({"dex": "tradexyz", "assetIds": asset_ids}))
    return p


def _full_ids():
    # mirrors config/deploy.example.json coin set
    return {"SNDK": 110000, "ARM": 110001, "NBIS": 110002, "DELL": 110003,
            "INTC": 110004, "CRWV": 110005, "SKHYNIX": 110006}


# ── config assembly ─────────────────────────────────────────────
def test_load_keeper_inputs_from_artifacts(tmp_path):
    inputs = load_keeper_inputs(BASKET, _write_deploy(tmp_path, _full_ids()))
    assert len(inputs.coins) == 7
    assert sum(inputs.target_weights.values()) == pytest.approx(1.0)
    assert set(inputs.asset_ids) == set(inputs.coins)          # filtered to basket
    assert all(c in inputs.sz_decimals for c in inputs.coins)
    assert inputs.dex == "tradexyz"


def test_load_keeper_inputs_missing_asset_id_raises(tmp_path):
    partial = {k: v for k, v in _full_ids().items() if k != "SKHYNIX"}
    with pytest.raises(KeyError, match="SKHYNIX"):
        load_keeper_inputs(BASKET, _write_deploy(tmp_path, partial))


# ── bot assembly ────────────────────────────────────────────────
def test_build_bot_preview_is_dry_run(tmp_path):
    inputs = load_keeper_inputs(BASKET, _write_deploy(tmp_path, _full_ids()))
    assert build_bot(inputs, client=object(), execute=False).dry_run is True
    assert build_bot(inputs, client=object(), execute=True).dry_run is False


def test_build_bot_threads_thresholds(tmp_path):
    inputs = load_keeper_inputs(BASKET, _write_deploy(tmp_path, _full_ids()))
    bot = build_bot(inputs, client=object(), execute=False,
                    buffer_fraction=0.1, drift_threshold=0.03, side="short")
    assert bot.config.buffer_fraction == 0.1
    assert bot.config.drift_threshold == 0.03
    assert bot.side == "short"


# ── report formatting ───────────────────────────────────────────
def _report(liq, reb):
    return KeeperReport(liquidity=liq, rebalance=reb)


def test_format_report_preview():
    r = _report(
        LiquidityResult(bridged=500.0, shortfall=0.0, submitted=False, verified=False),
        RebalanceResult(triggered=True, orders=[1, 2, 3], submitted=False),
    )
    s = format_report(r)
    assert "would bridge 500.00 [preview]" in s and "3 legs [preview]" in s


def test_format_report_executed_and_flags():
    r = _report(
        LiquidityResult(bridged=500.0, shortfall=120.0, submitted=True, verified=False),
        RebalanceResult(triggered=False, verified=True),
    )
    s = format_report(r)
    assert "UNVERIFIED" in s and "shortfall 120.00" in s and "rebalance: ok" in s


def test_format_report_all_ok():
    r = _report(
        LiquidityResult(bridged=0.0, shortfall=0.0, submitted=False, verified=True),
        RebalanceResult(triggered=False, verified=True),
    )
    assert format_report(r) == "liquidity: ok | rebalance: ok"


# ── main() end-to-end (fake client factory, no web3) ────────────
class FakeClient:
    """Balanced book + comfortable liquidity -> a clean no-op tick."""

    def idle_assets(self): return 1_000_000.0
    def pending_redeem_assets(self): return 0.0
    def nav(self): return 1_000_000.0
    def core_available(self): return 0.0
    def positions(self): return {c: 1.0 for c in _full_ids()}
    def prices(self): return {c: 10.0 for c in _full_ids()}


def test_main_preview_single_tick(tmp_path, capsys, monkeypatch):
    deploy = _write_deploy(tmp_path, _full_ids())
    captured = {}

    def factory(**kw):
        captured.update(kw)
        return FakeClient()

    rc = main(
        ["--basket", str(BASKET), "--deploy", str(deploy),
         "--rpc-url", "http://node", "--vault", "0xVAULT", "--usdc", "0xUSDC", "--once"],
        client_factory=factory,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "PREVIEW — nothing sent" in out
    assert "liquidity: ok | rebalance: ok" in out
    assert captured["private_key"] is None        # preview never passes the key


def test_main_requires_addresses(tmp_path, monkeypatch):
    monkeypatch.delenv("RPC_URL", raising=False)
    monkeypatch.delenv("VAULT_ADDRESS", raising=False)
    monkeypatch.delenv("HL_VAULT_ADDRESS", raising=False)
    monkeypatch.delenv("USDC_ADDRESS", raising=False)
    deploy = _write_deploy(tmp_path, _full_ids())
    rc = main(["--basket", str(BASKET), "--deploy", str(deploy)], client_factory=lambda **k: None)
    assert rc == 2


def test_main_missing_deploy_file_is_clean_error(tmp_path, capsys):
    rc = main(
        ["--basket", str(BASKET), "--deploy", str(tmp_path / "nope.json"),
         "--rpc-url", "http://n", "--vault", "0xV", "--usdc", "0xU", "--once"],
        client_factory=lambda **k: FakeClient(),
    )
    assert rc == 2
    assert "config file not found" in capsys.readouterr().out


def test_main_execute_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGER_KEY", raising=False)
    monkeypatch.delenv("HL_SECRET_KEY", raising=False)
    deploy = _write_deploy(tmp_path, _full_ids())
    rc = main(
        ["--basket", str(BASKET), "--deploy", str(deploy), "--rpc-url", "http://n",
         "--vault", "0xV", "--usdc", "0xU", "--execute", "--once"],
        client_factory=lambda **k: FakeClient(),
    )
    assert rc == 2
