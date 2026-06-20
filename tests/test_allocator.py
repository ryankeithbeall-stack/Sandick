
import pytest

from sandick.allocator import build_equal_weight_plan, round_size
from sandick.basket import Basket


def _basket():
    return Basket.from_dict(
        {
            "name": "TEST",
            "dex": "test",
            "assets": [
                {"company": "A Co", "ticker": "AAA", "coin": "AAA", "sz_decimals": 2},
                {"company": "B Co", "ticker": "BBB", "coin": "BBB", "sz_decimals": 2},
                {"company": "C Co", "ticker": "CCC", "coin": "CCC", "sz_decimals": 2},
                {"company": "D Co", "ticker": "DDD", "coin": "DDD", "sz_decimals": 2},
            ],
        }
    )


def _prices():
    return {"AAA": 10.0, "BBB": 20.0, "CCC": 50.0, "DDD": 100.0}


def test_round_size_floors_to_precision():
    assert round_size(1.2399, 2) == 1.23
    assert round_size(1.2, 0) == 1.0
    assert round_size(0.009, 2) == 0.0


def test_equal_weight_targets_are_one_over_n():
    plan = build_equal_weight_plan(_basket(), _prices(), capital=1000.0)
    assert all(o.target_weight == pytest.approx(0.25) for o in plan.orders)
    # Each asset targets the same gross notional.
    assert all(o.target_notional == pytest.approx(250.0) for o in plan.orders)


def test_sizes_match_target_notional_at_1x():
    plan = build_equal_weight_plan(_basket(), _prices(), capital=1000.0, leverage=1.0)
    sizes = {o.asset.coin: o.size for o in plan.orders}
    # 250 / price, floored to 2dp.
    assert sizes["AAA"] == 25.0
    assert sizes["BBB"] == 12.5
    assert sizes["CCC"] == 5.0
    assert sizes["DDD"] == 2.5


def test_leverage_scales_gross_notional():
    plan = build_equal_weight_plan(_basket(), _prices(), capital=1000.0, leverage=3.0)
    assert plan.gross_notional == pytest.approx(3000.0, abs=1.0)
    # Margin deployed should be ~ capital, not gross.
    assert plan.deployed_margin == pytest.approx(1000.0, abs=1.0)


def test_actual_weights_sum_to_one():
    plan = build_equal_weight_plan(_basket(), _prices(), capital=12345.0)
    assert sum(o.actual_weight for o in plan.orders) == pytest.approx(1.0)


def test_residual_cash_is_non_negative_and_small():
    plan = build_equal_weight_plan(_basket(), _prices(), capital=1000.0)
    assert plan.residual_cash >= 0
    assert plan.residual_cash < plan.capital


def test_missing_price_raises():
    prices = _prices()
    del prices["AAA"]
    with pytest.raises(KeyError):
        build_equal_weight_plan(_basket(), prices, capital=1000.0)


@pytest.mark.parametrize("bad", [0, -100])
def test_invalid_capital_raises(bad):
    with pytest.raises(ValueError):
        build_equal_weight_plan(_basket(), _prices(), capital=bad)


def test_invalid_leverage_raises():
    with pytest.raises(ValueError):
        build_equal_weight_plan(_basket(), _prices(), capital=1000.0, leverage=0)


def test_short_side_supported():
    plan = build_equal_weight_plan(_basket(), _prices(), capital=1000.0, side="short")
    assert all(o.side == "short" for o in plan.orders)
