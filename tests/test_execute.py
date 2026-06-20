import pytest

from sandick.allocator import build_plan
from sandick.basket import Basket
from sandick.execute import (
    ExecConfig,
    check_safety,
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
