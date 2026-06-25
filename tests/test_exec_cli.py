import json

from sandick import exec_cli


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


# ---- run ----------------------------------------------------------------

def test_run_preview_is_default_and_sends_nothing(tmp_path, capsys, monkeypatch):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)

    def boom(*a, **k):  # submit must not be called in a preview
        raise AssertionError("submit() should not run in preview mode")

    monkeypatch.setattr(exec_cli, "submit", boom)
    rc = exec_cli.main(
        ["run", "--capital", "1000", "--basket", str(basket), "--prices", str(prices)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "preview only" in out
    assert "TOTAL gross notional" in out


def test_run_live_resolves_prices_from_sdk(tmp_path, capsys, install_hyperliquid):
    basket = _write_basket(tmp_path)
    install_hyperliquid(mids={"SNDK": "50.0", "INTC": "22.0"})
    rc = exec_cli.main(["run", "--capital", "1000", "--basket", str(basket), "--live"])
    assert rc == 0
    assert "TOTAL gross notional" in capsys.readouterr().out


def test_run_rejects_over_max_notional(tmp_path, capsys):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    rc = exec_cli.main(
        [
            "run", "--capital", "1000", "--basket", str(basket),
            "--prices", str(prices), "--max-notional", "100",
        ]
    )
    assert rc == 2
    assert "exceeds --max-notional" in capsys.readouterr().err


def test_run_requires_price_source(tmp_path, capsys):
    basket = _write_basket(tmp_path)
    rc = exec_cli.main(["run", "--capital", "1000", "--basket", str(basket)])
    assert rc == 2
    assert "--prices" in capsys.readouterr().err


def test_run_execute_confirm_abort(tmp_path, capsys, monkeypatch):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_: "no")

    def boom(*a, **k):
        raise AssertionError("must not submit after abort")

    monkeypatch.setattr(exec_cli, "submit", boom)
    rc = exec_cli.main(
        ["run", "--capital", "1000", "--basket", str(basket), "--prices", str(prices), "--execute"]
    )
    assert rc == 1
    assert "aborted" in capsys.readouterr().out


def test_run_execute_with_yes_calls_submit(tmp_path, capsys, monkeypatch):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    captured = {}

    def fake_submit(intents, config):
        captured["n"] = len(intents)
        captured["testnet"] = config.testnet
        return [
            {"coin": i.coin, "result": "ok",
             "outcome": {"accepted": True, "state": "filled", "detail": "filled",
                         "oid": 1, "filled_sz": 1.0, "avg_px": 10.0}}
            for i in intents
        ]

    monkeypatch.setattr(exec_cli, "submit", fake_submit)
    rc = exec_cli.main(
        [
            "run", "--capital", "1000", "--basket", str(basket),
            "--prices", str(prices), "--execute", "--yes",
        ]
    )
    assert rc == 0
    assert captured["n"] == 2
    assert captured["testnet"] is True  # defaults to testnet


def test_run_execute_submit_failure_returns_2(tmp_path, capsys, monkeypatch):
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)

    def fake_submit(intents, config):
        raise RuntimeError("node rejected")

    monkeypatch.setattr(exec_cli, "submit", fake_submit)
    rc = exec_cli.main(
        [
            "run", "--capital", "1000", "--basket", str(basket),
            "--prices", str(prices), "--execute", "--yes",
        ]
    )
    assert rc == 2
    assert "submission failed" in capsys.readouterr().err


def _run_execute(tmp_path, monkeypatch, outcomes):
    """Run `run --execute --yes` with submit() faked to return the given per-leg
    outcomes (one dict per leg, in coin order SNDK, INTC)."""
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)

    def fake_submit(intents, config):
        return [
            {"coin": i.coin, "result": "x", "outcome": outcomes[n]}
            for n, i in enumerate(intents)
        ]

    monkeypatch.setattr(exec_cli, "submit", fake_submit)
    return exec_cli.main(
        ["run", "--capital", "1000", "--basket", str(basket),
         "--prices", str(prices), "--execute", "--yes"]
    )


def test_run_execute_all_filled_returns_0(tmp_path, capsys, monkeypatch):
    filled = {"accepted": True, "state": "filled", "detail": "filled",
              "oid": 1, "filled_sz": 1.0, "avg_px": 10.0}
    rc = _run_execute(tmp_path, monkeypatch, [filled, filled])
    out = capsys.readouterr().out
    assert rc == 0
    assert "all 2 leg(s) filled" in out
    assert "[ OK ]" in out


def test_run_execute_rejected_leg_returns_1(tmp_path, capsys, monkeypatch):
    filled = {"accepted": True, "state": "filled", "detail": "filled",
              "oid": 1, "filled_sz": 1.0, "avg_px": 10.0}
    rejected = {"accepted": False, "state": "rejected",
                "detail": "price too far from reference",
                "oid": None, "filled_sz": None, "avg_px": None}
    rc = _run_execute(tmp_path, monkeypatch, [filled, rejected])
    captured = capsys.readouterr()
    assert rc == 1
    assert "[FAIL]" in captured.out
    assert "1 of 2 leg(s) rejected" in captured.err


def test_run_execute_resting_leg_warns_but_returns_0(tmp_path, capsys, monkeypatch):
    filled = {"accepted": True, "state": "filled", "detail": "filled",
              "oid": 1, "filled_sz": 1.0, "avg_px": 10.0}
    resting = {"accepted": True, "state": "resting",
               "detail": "resting (accepted, no immediate fill)",
               "oid": 9, "filled_sz": 0.0, "avg_px": None}
    rc = _run_execute(tmp_path, monkeypatch, [filled, resting])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[WARN]" in out
    assert "not immediately filled" in out


def test_run_execute_unclassified_leg_returns_1(tmp_path, capsys, monkeypatch):
    # A result with no outcome (unclassified) must NOT pass silently as success.
    basket = _write_basket(tmp_path)
    prices = _write_prices(tmp_path)
    monkeypatch.setattr(
        exec_cli, "submit",
        lambda intents, config: [{"coin": i.coin, "result": "?"} for i in intents],
    )
    rc = exec_cli.main(
        ["run", "--capital", "1000", "--basket", str(basket),
         "--prices", str(prices), "--execute", "--yes"]
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "unclassified" in captured.out
    assert "rejected" in captured.err


# ---- verify -------------------------------------------------------------

def test_verify_passes_when_coins_present_and_vault_readable(tmp_path, capsys, install_hyperliquid):
    basket = _write_basket(tmp_path)
    install_hyperliquid(
        meta={"universe": [{"name": "SNDK"}, {"name": "INTC"}]},
        user_state={"withdrawable": "1000.0"},
    )
    rc = exec_cli.main(["verify", "--basket", str(basket), "--vault", "0xabcdef0123456789"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASSED" in out
    assert "all 2 basket coins present" in out


def test_verify_fails_when_coin_missing(tmp_path, capsys, install_hyperliquid):
    basket = _write_basket(tmp_path)
    install_hyperliquid(meta={"universe": [{"name": "SNDK"}]})  # INTC missing
    rc = exec_cli.main(["verify", "--basket", str(basket)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "coins not on dex" in out


def test_verify_fails_when_meta_unreadable(tmp_path, capsys, install_hyperliquid):
    basket = _write_basket(tmp_path)
    install_hyperliquid(meta=RuntimeError("dex down"))
    rc = exec_cli.main(["verify", "--basket", str(basket)])
    assert rc == 1
    assert "could not load dex meta" in capsys.readouterr().out


def test_verify_fails_when_vault_state_unreadable(tmp_path, capsys, install_hyperliquid):
    basket = _write_basket(tmp_path)
    install_hyperliquid(
        meta={"universe": [{"name": "SNDK"}, {"name": "INTC"}]},
        user_state=RuntimeError("no such account"),
    )
    rc = exec_cli.main(["verify", "--basket", str(basket), "--vault", "0xabc1234567"])
    assert rc == 1
    assert "could not read vault state" in capsys.readouterr().out


def test_verify_skips_vault_check_without_address(tmp_path, capsys, install_hyperliquid):
    basket = _write_basket(tmp_path)
    install_hyperliquid(meta={"universe": [{"name": "SNDK"}, {"name": "INTC"}]})
    rc = exec_cli.main(["verify", "--basket", str(basket)])
    assert rc == 0
    assert "[SKIP]" in capsys.readouterr().out


def test_verify_reports_missing_sdk(tmp_path, capsys, monkeypatch):
    basket = _write_basket(tmp_path)
    # Force the SDK import to fail even if it happens to be installed.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("hyperliquid"):
            raise ImportError("no sdk")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    rc = exec_cli.main(["verify", "--basket", str(basket)])
    assert rc == 2
    assert "not installed" in capsys.readouterr().err


# ---- rendering ----------------------------------------------------------

def test_render_intents_formats_table(tmp_path):
    from sandick.allocator import build_plan
    from sandick.basket import Basket
    from sandick.execute import plan_to_intents

    basket = Basket.load(_write_basket(tmp_path))
    plan = build_plan(basket, {"SNDK": 50.0, "INTC": 22.0}, capital=1000.0)
    text = exec_cli._render_intents(plan_to_intents(plan))
    assert "COIN" in text and "NOTIONAL" in text
    assert "TOTAL gross notional" in text
