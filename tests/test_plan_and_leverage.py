import json

import pytest

from sandick.allocator import build_plan
from sandick.basket import Basket
from sandick.plan import PLAN_SCHEMA_VERSION, plan_to_dict, write_plan


def _a(coin, price_decimals=2, **kw):
    return {"company": coin, "ticker": coin, "coin": coin, "sz_decimals": 2, **kw}


def _basket(assets, groups=None):
    data = {"name": "T", "dex": "t", "assets": assets}
    if groups:
        data["groups"] = groups
    return Basket.from_dict(data)


PRICES = {"A": 10.0, "B": 20.0, "C": 50.0, "D": 100.0}


def test_per_asset_leverage_changes_margin_not_weight():
    # Two assets, equal weight, but A at 2x and B at 1x.
    b = _basket([_a("A", leverage=2), _a("B", leverage=1)])
    plan = build_plan(b, {"A": 10.0, "B": 20.0}, capital=1000.0)
    # Equal notional weighting preserved.
    assert all(o.actual_weight == pytest.approx(0.5, abs=1e-3) for o in plan.orders)
    # Total margin still ~ capital.
    assert plan.deployed_margin == pytest.approx(1000.0, abs=1.0)
    # A uses half the margin of B for the same notional (2x vs 1x).
    margins = {o.asset.coin: o.margin for o in plan.orders}
    assert margins["A"] == pytest.approx(margins["B"] / 2, rel=1e-2)


def test_leverage_over_max_raises():
    b = _basket([_a("A", leverage=10, max_leverage=5)])
    with pytest.raises(ValueError):
        build_plan(b, {"A": 10.0}, capital=1000.0)


def test_grouped_basket_sizing():
    b = _basket(
        [_a("A", group="x"), _a("B", group="x"), _a("C", group="y")],
        groups={"x": 0.5, "y": 0.5},
    )
    plan = build_plan(b, PRICES, capital=1000.0)
    weights = {o.asset.coin: o.actual_weight for o in plan.orders}
    assert weights["C"] == pytest.approx(0.5, abs=1e-2)
    assert weights["A"] == pytest.approx(0.25, abs=1e-2)


def test_plan_serialization_roundtrip(tmp_path):
    b = _basket([_a("A"), _a("B"), _a("C"), _a("D")])
    plan = build_plan(b, PRICES, capital=1000.0, leverage=2.0, side="short")
    d = plan_to_dict(plan)
    assert d["schema_version"] == PLAN_SCHEMA_VERSION
    assert d["side"] == "short"
    assert len(d["orders"]) == 4
    assert d["orders"][0]["coin"] == "A"

    out = tmp_path / "plan.json"
    write_plan(plan, str(out))
    reloaded = json.loads(out.read_text())
    assert reloaded["basket"] == "T"
    assert reloaded["default_leverage"] == 2.0
