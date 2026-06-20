import pytest

from sandick.allocator import build_plan
from sandick.basket import Basket
from sandick.onchain import (
    hip3_asset_id,
    plan_to_onchain_orders,
    to_core_int,
)


def test_hip3_asset_id_first_builder_dex():
    # First builder dex (index 1), first asset -> 110000 (docs example).
    assert hip3_asset_id(1, 0) == 110000
    assert hip3_asset_id(1, 5) == 110005
    assert hip3_asset_id(2, 0) == 120000


def test_hip3_asset_id_validates():
    with pytest.raises(ValueError):
        hip3_asset_id(0, 0)  # builder dex index starts at 1
    with pytest.raises(ValueError):
        hip3_asset_id(1, 10_000)  # outside the per-dex block
    with pytest.raises(ValueError):
        hip3_asset_id(1, -1)  # negative index_in_meta


def test_to_core_int_scales_1e8():
    assert to_core_int(50.0) == 5_000_000_000
    assert to_core_int(0.123) == 12_300_000


def _basket():
    return Basket.from_dict(
        {
            "name": "T",
            "dex": "tradexyz",
            "assets": [
                {"company": "S", "ticker": "SNDK", "coin": "SNDK", "sz_decimals": 2},
                {"company": "I", "ticker": "INTC", "coin": "INTC", "sz_decimals": 1},
            ],
        }
    )


def test_plan_to_onchain_orders():
    plan = build_plan(_basket(), {"SNDK": 50.0, "INTC": 22.0}, capital=1000.0, side="long")
    ids = {"SNDK": 110000, "INTC": 110001}
    orders = plan_to_onchain_orders(plan, ids, slippage=0.02)
    assert {o.asset_id for o in orders} == {110000, 110001}
    assert all(o.is_buy for o in orders)
    # buy limit is above mark, scaled by 1e8
    sndk = next(o for o in orders if o.asset_id == 110000)
    assert sndk.limit_px > to_core_int(50.0)
    # tuple ordering matches the Solidity Order struct
    assert sndk.as_tuple() == (110000, True, sndk.limit_px, sndk.sz, False)


def test_plan_to_onchain_orders_missing_id_raises():
    plan = build_plan(_basket(), {"SNDK": 50.0, "INTC": 22.0}, capital=1000.0)
    with pytest.raises(KeyError):
        plan_to_onchain_orders(plan, {"SNDK": 110000}, slippage=0.01)


def test_plan_to_onchain_orders_skips_zero_size_legs():
    # Capital so small that INTC (1dp) rounds to zero size but SNDK (2dp) does not.
    plan = build_plan(_basket(), {"SNDK": 50.0, "INTC": 22.0}, capital=1.0)
    ids = {"SNDK": 110000, "INTC": 110001}
    orders = plan_to_onchain_orders(plan, ids, slippage=0.02)
    assert {o.asset_id for o in orders} == {110000}  # INTC dropped
