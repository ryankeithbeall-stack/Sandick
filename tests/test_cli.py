import json

import pytest

from sandick import cli


def _write_basket(tmp_path):
    basket = {
        "name": "SANDICK",
        "dex": "tradexyz",
        "assets": [
            {"company": "SanDisk", "ticker": "SNDK", "coin": "SNDK", "sz_decimals": 2},
            {"company": "Intel", "ticker": "INTC", "coin": "INTC", "sz_decimals": 1},
        ],
    }
    p = tmp_path / "basket.json"
    p.write_text(json.dumps(basket))
    return p


def _write_prices(tmp_path):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"SNDK": 50.0, "INTC": 22.0}))
    return p


def test_main_happy_path_prints_plan(tmp_path, capsys):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    rc = cli.main(["--capital", "1000", "--basket", str(basket), "--prices", str(prices)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SANDICK HIP-3 VAULT" in out
    assert "DRY RUN" in out
    assert "SNDK" in out and "INTC" in out


def test_main_writes_artifact_with_out(tmp_path, capsys):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    out = tmp_path / "plan.json"
    rc = cli.main(
        ["--capital", "1000", "--basket", str(basket), "--prices", str(prices), "--out", str(out)]
    )
    assert rc == 0
    assert "Saved plan artifact" in capsys.readouterr().out
    saved = json.loads(out.read_text())
    assert saved["basket"] == "SANDICK"
    assert len(saved["orders"]) == 2


def test_main_requires_a_price_source(tmp_path, capsys):
    basket = _write_basket(tmp_path)
    rc = cli.main(["--capital", "1000", "--basket", str(basket)])
    assert rc == 2
    assert "provide a price source" in capsys.readouterr().err


def test_main_reports_missing_prices(tmp_path, capsys):
    basket = _write_basket(tmp_path)
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"SNDK": 50.0}))  # INTC missing
    rc = cli.main(["--capital", "1000", "--basket", str(basket), "--prices", str(p)])
    assert rc == 2
    assert "error:" in capsys.readouterr().err


def test_main_reports_invalid_capital(tmp_path, capsys):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    rc = cli.main(["--capital", "0", "--basket", str(basket), "--prices", str(prices)])
    assert rc == 2
    assert "error:" in capsys.readouterr().err


def test_main_live_failure_is_surfaced_cleanly(tmp_path, capsys, monkeypatch):
    basket = _write_basket(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(cli, "fetch_live_prices", boom)
    rc = cli.main(["--capital", "1000", "--basket", str(basket), "--live"])
    assert rc == 2
    assert "failed to fetch live prices" in capsys.readouterr().err


def test_main_live_uses_fetch(tmp_path, monkeypatch, capsys):
    basket = _write_basket(tmp_path)
    captured = {}

    def fake_fetch(coins, dex="", mainnet=True):
        captured["coins"] = coins
        captured["dex"] = dex
        captured["mainnet"] = mainnet
        return {"SNDK": 50.0, "INTC": 22.0}

    monkeypatch.setattr(cli, "fetch_live_prices", fake_fetch)
    rc = cli.main(["--capital", "1000", "--basket", str(basket), "--live", "--testnet"])
    assert rc == 0
    assert captured["coins"] == ["SNDK", "INTC"]
    assert captured["dex"] == "tradexyz"
    assert captured["mainnet"] is False  # --testnet flips mainnet off


def test_prices_and_live_are_mutually_exclusive(tmp_path):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(
            ["--capital", "1000", "--basket", str(basket), "--prices", str(prices), "--live"]
        )


def test_render_plan_includes_totals_and_weights(tmp_path):
    from sandick.allocator import build_plan
    from sandick.basket import Basket

    basket = Basket.load(_write_basket(tmp_path))
    plan = build_plan(basket, {"SNDK": 50.0, "INTC": 22.0}, capital=1000.0)
    text = cli.render_plan(plan)
    assert "TOTAL" in text
    assert "Deployed margin" in text
    assert "Residual cash" in text
    # the weight column renders as a percentage
    assert "%" in text
