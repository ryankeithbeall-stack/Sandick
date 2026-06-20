import pytest

from sandick.keeper import (
    KeeperConfig,
    max_weight_drift,
    needs_rebalance,
    plan_liquidity,
    weights_from_positions,
)


def test_config_validation():
    with pytest.raises(ValueError):
        KeeperConfig(buffer_fraction=1.0)
    with pytest.raises(ValueError):
        KeeperConfig(buffer_fraction=-0.1)
    with pytest.raises(ValueError):
        KeeperConfig(drift_threshold=-0.01)


def test_plan_liquidity_no_bridge_when_idle_covers_need():
    # idle 1000 >= pending 200 + buffer 5% * 10000 = 500 -> 700 need
    a = plan_liquidity(idle_assets=1000, pending_redeem_assets=200, nav=10_000,
                       core_available=5000)
    assert a.bridge_from_core == 0.0
    assert a.idle_after == 1000
    assert a.shortfall == 0.0


def test_plan_liquidity_bridges_the_deficit():
    # need = 600 pending + 0.05*10000 buffer (500) = 1100; idle 100 -> deficit 1000
    a = plan_liquidity(idle_assets=100, pending_redeem_assets=600, nav=10_000,
                       core_available=5000)
    assert a.bridge_from_core == 1000.0
    assert a.idle_after == 1100.0
    assert a.shortfall == 0.0


def test_plan_liquidity_reports_shortfall_when_core_cannot_cover():
    # deficit 1000 but only 300 available on Core
    a = plan_liquidity(idle_assets=100, pending_redeem_assets=600, nav=10_000,
                       core_available=300)
    assert a.bridge_from_core == 300.0
    assert a.idle_after == 400.0
    assert a.shortfall == 700.0


def test_plan_liquidity_rejects_negative_inputs():
    with pytest.raises(ValueError):
        plan_liquidity(idle_assets=-1, pending_redeem_assets=0, nav=1, core_available=0)


def test_weights_from_positions_abs_notional_sums_to_one():
    w = weights_from_positions(
        positions={"A": 10.0, "B": -5.0},   # B is short, still weighted
        prices={"A": 100.0, "B": 200.0},    # A: 1000, B: 1000 -> 50/50
    )
    assert w["A"] == pytest.approx(0.5)
    assert w["B"] == pytest.approx(0.5)
    assert sum(w.values()) == pytest.approx(1.0)


def test_weights_from_positions_empty_book():
    w = weights_from_positions(positions={"A": 0.0}, prices={"A": 100.0})
    assert w == {"A": 0.0}


def test_drift_and_rebalance_signal():
    target = {"A": 0.5, "B": 0.5}
    near = {"A": 0.51, "B": 0.49}      # 1pt drift
    far = {"A": 0.6, "B": 0.4}         # 10pt drift
    assert max_weight_drift(near, target) == pytest.approx(0.01)
    assert not needs_rebalance(near, target)            # under default 2pt
    assert needs_rebalance(far, target)                 # over threshold


def test_rebalance_threshold_is_configurable():
    target = {"A": 0.5, "B": 0.5}
    cur = {"A": 0.515, "B": 0.485}     # 1.5pt drift
    assert not needs_rebalance(cur, target, KeeperConfig(drift_threshold=0.02))
    assert needs_rebalance(cur, target, KeeperConfig(drift_threshold=0.01))


def test_missing_coin_counts_as_zero_weight():
    # target holds C at 0.2 but current book has nothing there -> full drift
    assert max_weight_drift({"A": 0.8}, {"A": 0.8, "C": 0.2}) == pytest.approx(0.2)
