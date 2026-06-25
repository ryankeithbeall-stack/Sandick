import pytest

import sandick.execute as execute_mod
from sandick.allocator import build_plan
from sandick.basket import Basket
from sandick.execute import (
    Credentials,
    ExecConfig,
    check_safety,
    interpret_order_result,
    marketable_limit,
    order_coin,
    plan_to_intents,
    round_price_perp,
    submit,
)


def _basket(dex="tradexyz"):
    return Basket.from_dict(
        {
            "name": "T",
            "dex": dex,
            "assets": [
                {"company": "S", "ticker": "SNDK", "coin": "SNDK", "sz_decimals": 2},
                {"company": "I", "ticker": "INTC", "coin": "INTC", "sz_decimals": 1},
            ],
        }
    )


PRICES = {"SNDK": 50.0, "INTC": 22.0}


def test_order_coin_qualifies_with_dex():
    assert order_coin("tradexyz", "SNDK") == "tradexyz:SNDK"
    assert order_coin("", "SNDK") == "SNDK"


def test_round_price_perp_five_sig_figs():
    assert round_price_perp(50.0, 2) == 50.0
    assert round_price_perp(1234.567, 2) == 1234.6  # 5 sig figs
    assert round_price_perp(0.123456, 2) == pytest.approx(0.1235, abs=1e-6)  # 6-2=4 dp cap


def test_marketable_limit_crosses_spread():
    # long pays up, short sells down
    assert marketable_limit(100.0, "long", 0.02, 2) == pytest.approx(102.0)
    assert marketable_limit(100.0, "short", 0.02, 2) == pytest.approx(98.0)


def test_plan_to_intents_buy_and_qualified_coins():
    plan = build_plan(_basket(), PRICES, capital=1000.0, side="long")
    intents = plan_to_intents(plan, slippage=0.01)
    coins = {i.coin for i in intents}
    assert coins == {"tradexyz:SNDK", "tradexyz:INTC"}
    assert all(i.is_buy for i in intents)
    # buy limit is above mark
    sndk = next(i for i in intents if i.coin.endswith("SNDK"))
    assert sndk.limit_px > 50.0


def test_plan_to_intents_short_sells():
    plan = build_plan(_basket(), PRICES, capital=1000.0, side="short")
    intents = plan_to_intents(plan)
    assert all(not i.is_buy for i in intents)


def test_check_safety_rejects_over_max_notional():
    plan = build_plan(_basket(), PRICES, capital=1000.0)
    intents = plan_to_intents(plan)
    with pytest.raises(ValueError):
        check_safety(intents, max_notional=100.0)  # plan is ~$1000


def test_check_safety_rejects_empty():
    with pytest.raises(ValueError):
        check_safety([], max_notional=None)


def test_submit_preview_is_noop_without_confirm():
    plan = build_plan(_basket(), PRICES, capital=1000.0)
    intents = plan_to_intents(plan)
    # confirm defaults to False -> must not touch the network / require creds
    results = submit(intents, ExecConfig())
    assert all(r["status"] == "preview" for r in results)


# ---- price/slippage edge cases -----------------------------------------

@pytest.mark.parametrize("bad_px", [0.0, -5.0])
def test_round_price_perp_rejects_non_positive(bad_px):
    with pytest.raises(ValueError):
        round_price_perp(bad_px, 2)


def test_marketable_limit_rejects_negative_slippage():
    with pytest.raises(ValueError):
        marketable_limit(100.0, "long", -0.01, 2)


def test_plan_to_intents_skips_zero_size_legs():
    # Capital too small for INTC at its 1dp size -> that leg rounds to zero.
    plan = build_plan(_basket(), PRICES, capital=1.0)
    intents = plan_to_intents(plan)
    assert all(i.size > 0 for i in intents)


def test_plan_to_intents_clamps_leverage_to_at_least_one():
    plan = build_plan(_basket(), PRICES, capital=1000.0, leverage=1.0)
    intents = plan_to_intents(plan)
    assert all(i.leverage >= 1 for i in intents)


# ---- credentials --------------------------------------------------------

def test_credentials_from_env_reads_all_fields(monkeypatch):
    monkeypatch.setenv("HL_SECRET_KEY", "0xdeadbeef")
    monkeypatch.setenv("HL_VAULT_ADDRESS", "0xvault")
    monkeypatch.setenv("HL_ACCOUNT_ADDRESS", "0xmaster")
    creds = Credentials.from_env()
    assert creds.secret_key == "0xdeadbeef"
    assert creds.vault_address == "0xvault"
    assert creds.account_address == "0xmaster"


def test_credentials_from_env_account_optional(monkeypatch):
    monkeypatch.setenv("HL_SECRET_KEY", "0xdeadbeef")
    monkeypatch.setenv("HL_VAULT_ADDRESS", "0xvault")
    monkeypatch.delenv("HL_ACCOUNT_ADDRESS", raising=False)
    assert Credentials.from_env().account_address is None


def test_credentials_from_env_requires_secret(monkeypatch):
    monkeypatch.delenv("HL_SECRET_KEY", raising=False)
    monkeypatch.setenv("HL_VAULT_ADDRESS", "0xvault")
    with pytest.raises(EnvironmentError):
        Credentials.from_env()


def test_credentials_from_env_requires_vault(monkeypatch):
    monkeypatch.setenv("HL_SECRET_KEY", "0xdeadbeef")
    monkeypatch.delenv("HL_VAULT_ADDRESS", raising=False)
    with pytest.raises(EnvironmentError):
        Credentials.from_env()


# ---- confirmed submission (SDK boundary faked) -------------------------

class _FakeExchange:
    def __init__(self):
        self.leverage_calls = []
        self.orders = []

    def update_leverage(self, leverage, coin, is_cross):
        self.leverage_calls.append((leverage, coin, is_cross))

    def order(self, coin, is_buy, size, limit_px, order_type, reduce_only=False):
        self.orders.append((coin, is_buy, size, limit_px, order_type, reduce_only))
        return {"status": "ok"}


def test_submit_confirmed_sets_leverage_then_orders(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TX", "1")
    plan = build_plan(_basket(), PRICES, capital=1000.0)
    intents = plan_to_intents(plan)
    fake = _FakeExchange()
    monkeypatch.setattr(execute_mod, "_make_exchange", lambda creds, testnet: fake)

    creds = Credentials(secret_key="k", account_address=None, vault_address="0xv")
    results = submit(intents, ExecConfig(confirm=True), creds=creds)

    assert len(results) == len(intents)
    # leverage is set before each order, once per leg
    assert len(fake.leverage_calls) == len(intents)
    assert len(fake.orders) == len(intents)
    # marketable-limit IOC, never reduce-only
    assert all(o[4] == {"limit": {"tif": "Ioc"}} for o in fake.orders)
    assert all(o[5] is False for o in fake.orders)


def test_submit_confirmed_enforces_max_notional_before_sending(monkeypatch):
    plan = build_plan(_basket(), PRICES, capital=1000.0)
    intents = plan_to_intents(plan)

    def boom(*a, **k):
        raise AssertionError("must not build an exchange when safety fails")

    monkeypatch.setattr(execute_mod, "_make_exchange", boom)
    creds = Credentials(secret_key="k", account_address=None, vault_address="0xv")
    with pytest.raises(ValueError):
        submit(intents, ExecConfig(confirm=True, max_notional=1.0), creds=creds)


def test_submit_rejects_empty_intents():
    with pytest.raises(ValueError):
        submit([], ExecConfig(confirm=True))


def test_submit_confirmed_requires_allow_live_tx_env(monkeypatch):
    # confirm=True but the hard env gate is unset -> refuse to broadcast
    monkeypatch.delenv("ALLOW_LIVE_TX", raising=False)
    plan = build_plan(_basket(), PRICES, capital=1000.0)
    intents = plan_to_intents(plan)
    creds = Credentials(secret_key="k", account_address=None, vault_address="0xv")
    monkeypatch.setattr(execute_mod, "_make_exchange",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not build exchange")))
    with pytest.raises(Exception, match="ALLOW_LIVE_TX"):
        submit(intents, ExecConfig(confirm=True), creds=creds)


# ---- order-result classification (fill verification) -------------------

def test_interpret_filled_order():
    res = {"status": "ok", "response": {"type": "order", "data": {"statuses": [
        {"filled": {"totalSz": "1.5", "avgPx": "100.25", "oid": 42}},
    ]}}}
    out = interpret_order_result(res)
    assert out["accepted"] is True
    assert out["state"] == "filled"
    assert out["oid"] == 42
    assert out["filled_sz"] == pytest.approx(1.5)
    assert out["avg_px"] == pytest.approx(100.25)


def test_interpret_resting_order_is_accepted_but_unfilled():
    res = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 7}}]}}}
    out = interpret_order_result(res)
    assert out["accepted"] is True
    assert out["state"] == "resting"
    assert out["oid"] == 7
    assert out["filled_sz"] == 0.0


def test_interpret_per_order_error_is_rejected():
    res = {"status": "ok", "response": {"data": {"statuses": [
        {"error": "Order price cannot be more than 95% away from reference"},
    ]}}}
    out = interpret_order_result(res)
    assert out["accepted"] is False
    assert out["state"] == "rejected"
    assert "95%" in out["detail"]


def test_interpret_top_level_error_is_not_accepted():
    res = {"status": "err", "response": "Insufficient margin to place order"}
    out = interpret_order_result(res)
    assert out["accepted"] is False
    assert out["state"] == "error"
    assert "Insufficient margin" in out["detail"]


def test_interpret_ok_without_statuses_is_not_accepted():
    # A bare {"status": "ok"} must NOT read as a successful fill.
    out = interpret_order_result({"status": "ok"})
    assert out["accepted"] is False
    assert out["state"] == "unknown"


def test_interpret_non_dict_and_unrecognized_are_not_accepted():
    assert interpret_order_result("boom")["accepted"] is False
    weird = {"status": "ok", "response": {"data": {"statuses": ["nope"]}}}
    assert interpret_order_result(weird)["accepted"] is False
    other = {"status": "ok", "response": {"data": {"statuses": [{"queued": {}}]}}}
    assert interpret_order_result(other)["accepted"] is False


def test_submit_attaches_outcome_per_leg(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TX", "1")
    plan = build_plan(_basket(), PRICES, capital=1000.0)
    intents = plan_to_intents(plan)

    class _FillExchange:
        def update_leverage(self, *a):  # noqa: D401
            pass

        def order(self, coin, *a, **k):
            return {"status": "ok", "response": {"data": {"statuses": [
                {"filled": {"totalSz": "1", "avgPx": "10", "oid": 1}}]}}}

    monkeypatch.setattr(execute_mod, "_make_exchange", lambda creds, testnet: _FillExchange())
    creds = Credentials(secret_key="k", account_address=None, vault_address="0xv")
    results = submit(intents, ExecConfig(confirm=True), creds=creds)
    assert all(r["outcome"]["accepted"] for r in results)
    assert all(r["outcome"]["state"] == "filled" for r in results)
