import pytest

from sandick.basket import Basket
from sandick.weights import resolve_weights


def _basket(assets, groups=None):
    return Basket.from_dict({"name": "T", "dex": "t", "assets": assets, **({"groups": groups} if groups else {})})


def _a(coin, **kw):
    return {"company": coin, "ticker": coin, "coin": coin, "sz_decimals": 2, **kw}


def test_equal_weight_default():
    b = _basket([_a("A"), _a("B"), _a("C"), _a("D")])
    w = resolve_weights(b)
    assert all(v == pytest.approx(0.25) for v in w.values())


def test_explicit_weights_normalized():
    b = _basket([_a("A", weight=3), _a("B", weight=1)])
    w = resolve_weights(b)
    assert w["A"] == pytest.approx(0.75)
    assert w["B"] == pytest.approx(0.25)


def test_partial_explicit_weights_raise():
    b = _basket([_a("A", weight=1), _a("B")])
    with pytest.raises(ValueError):
        resolve_weights(b)


def test_grouped_weights_split_within_group():
    b = _basket(
        [_a("A", group="x"), _a("B", group="x"), _a("C", group="y")],
        groups={"x": 0.5, "y": 0.5},
    )
    w = resolve_weights(b)
    assert w["A"] == pytest.approx(0.25)
    assert w["B"] == pytest.approx(0.25)
    assert w["C"] == pytest.approx(0.5)
    assert sum(w.values()) == pytest.approx(1.0)


def test_grouped_missing_group_assignment_raises():
    b = _basket([_a("A", group="x"), _a("B")], groups={"x": 1.0})
    with pytest.raises(ValueError):
        resolve_weights(b)


def test_grouped_undefined_group_raises():
    b = _basket([_a("A", group="z")], groups={"x": 1.0})
    with pytest.raises(ValueError):
        resolve_weights(b)


def test_weights_always_sum_to_one():
    b = _basket([_a("A", weight=7), _a("B", weight=11), _a("C", weight=2)])
    assert sum(resolve_weights(b).values()) == pytest.approx(1.0)
