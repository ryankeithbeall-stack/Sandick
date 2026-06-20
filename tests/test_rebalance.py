from sandick.allocator import build_plan
from sandick.basket import Basket
from sandick.rebalance import compute_rebalance, rebalance_to_onchain, targets_from_plan

SZ = {"A": 2, "B": 2, "C": 2}


def test_open_from_flat_is_buy_not_reduce_only():
    orders = compute_rebalance({"A": 10.0}, {}, SZ)
    assert len(orders) == 1
    o = orders[0]
    assert o.is_buy and not o.reduce_only and o.size == 10.0


def test_increase_long_is_buy_not_reduce_only():
    orders = compute_rebalance({"A": 15.0}, {"A": 10.0}, SZ)
    assert orders[0].is_buy and not orders[0].reduce_only and orders[0].size == 5.0


def test_decrease_long_is_sell_reduce_only():
    orders = compute_rebalance({"A": 6.0}, {"A": 10.0}, SZ)
    o = orders[0]
    assert (not o.is_buy) and o.reduce_only and o.size == 4.0


def test_close_long_fully_reduce_only():
    orders = compute_rebalance({"A": 0.0}, {"A": 10.0}, SZ)
    assert orders[0].reduce_only and not orders[0].is_buy and orders[0].size == 10.0


def test_no_op_when_at_target():
    assert compute_rebalance({"A": 10.0}, {"A": 10.0}, SZ) == []


def test_min_size_filters_dust():
    assert compute_rebalance({"A": 10.01}, {"A": 10.0}, SZ, min_size=0.05) == []


def test_reduce_short_via_buy_reduce_only():
    orders = compute_rebalance({"A": -2.0}, {"A": -10.0}, SZ)
    o = orders[0]
    assert o.is_buy and o.reduce_only and o.size == 8.0


def test_rebalance_to_onchain_carries_reduce_only():
    orders = compute_rebalance({"A": 6.0}, {"A": 10.0}, SZ)  # sell 4, reduce-only
    onchain = rebalance_to_onchain(
        orders, {"A": 110000}, {"A": 50.0}, SZ, slippage=0.02
    )
    assert len(onchain) == 1
    oc = onchain[0]
    assert oc.asset_id == 110000 and not oc.is_buy and oc.reduce_only
    assert oc.limit_px < 50 * 10**8  # sell crosses down


def test_targets_from_plan_signs():
    b = Basket.from_dict(
        {
            "name": "T", "dex": "d",
            "assets": [{"company": "A", "ticker": "A", "coin": "A", "sz_decimals": 2}],
        }
    )
    long_t = targets_from_plan(build_plan(b, {"A": 10.0}, capital=100.0, side="long"))
    short_t = targets_from_plan(build_plan(b, {"A": 10.0}, capital=100.0, side="short"))
    assert long_t["A"] > 0 and short_t["A"] < 0
