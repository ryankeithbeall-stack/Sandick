"""Tests for the fail-closed keeper gate (keeper_guard.py)."""

from sandick.keeper_guard import KeeperState, evaluate_gate


def _state(**kw):
    base = dict(idle=100.0, pending_redeem=50.0, nav=10_000.0, core_available=500.0,
                positions={"A": 5.0}, prices={"A": 10.0})
    base.update(kw)
    return KeeperState(**base)


def test_coherent_state_is_allowed():
    assert evaluate_gate(_state()).allowed is True


def test_empty_vault_nav_zero_is_allowed():
    # a brand-new vault: nothing to do, nav==0 must NOT block
    g = evaluate_gate(_state(idle=0.0, pending_redeem=0.0, nav=0.0,
                             core_available=0.0, positions={}, prices={}))
    assert g.allowed is True and g.blockers == ()


def test_idle_equal_to_nav_is_allowed():
    # strict idle>nav only — idle==nav (all funds idle) must pass
    assert evaluate_gate(_state(idle=10_000.0, nav=10_000.0)).allowed is True


def test_idle_exceeds_nav_blocks():
    g = evaluate_gate(_state(idle=10_001.0, nav=10_000.0))
    assert g.allowed is False
    assert any("exceeds NAV" in b for b in g.blockers)


def test_idle_positive_with_zero_nav_blocks():
    g = evaluate_gate(_state(idle=100.0, nav=0.0, pending_redeem=0.0, positions={}, prices={}))
    assert g.allowed is False  # idle>0 but nav==0 is a contradiction


def test_negative_reads_block():
    for field in ("nav", "idle", "pending_redeem", "core_available"):
        g = evaluate_gate(_state(**{field: -1.0}))
        assert g.allowed is False, field
        assert any(field in b for b in g.blockers)


def test_missing_price_for_position_blocks():
    g = evaluate_gate(_state(positions={"A": 5.0, "B": 2.0}, prices={"A": 10.0}))
    assert g.allowed is False
    assert any("missing price" in b and "B" in b for b in g.blockers)


def test_nonpositive_price_for_open_position_blocks():
    g = evaluate_gate(_state(positions={"A": 5.0}, prices={"A": 0.0}))
    assert g.allowed is False
    assert any("non-positive price" in b for b in g.blockers)


def test_pending_redeem_with_zero_nav_blocks():
    g = evaluate_gate(_state(idle=0.0, pending_redeem=100.0, nav=0.0, positions={}, prices={}))
    assert g.allowed is False
    assert any("pending redemptions but NAV is 0" in b for b in g.blockers)
