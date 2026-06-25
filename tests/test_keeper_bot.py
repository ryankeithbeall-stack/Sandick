"""Tests for the keeper bot orchestration (keeper_bot.py).

The bot talks to the vault through the KeeperClient protocol; here we drive it
with a programmable fake so the read -> act -> verify loop is exercised fully
offline.
"""

from typing import Dict, List

import pytest

from sandick.keeper import KeeperConfig
from sandick.keeper_bot import (
    KeeperBot,
    gross_notional,
    target_sizes_from_weights,
)
from sandick.onchain import OnchainOrder

EQUAL = {c: 1 / 4 for c in ("A", "B", "C", "D")}
SZ = {c: 2 for c in EQUAL}
IDS = {"A": 110001, "B": 110002, "C": 110003, "D": 110004}
PRICES = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0}


class FakeClient:
    """Programmable KeeperClient. ``idle_steps``/``pos_steps`` let a test model
    state that only changes *after* a write settles (async verification)."""

    def __init__(
        self,
        idle=0.0,
        pending=0.0,
        nav=0.0,
        core=0.0,
        positions=None,
        prices=None,
        idle_after_bridge=None,
        positions_after_submit=None,
    ):
        self._idle = idle
        self._pending = pending
        self._nav = nav
        self._core = core
        self._positions = dict(positions or {})
        self._prices = dict(prices or PRICES)
        self._idle_after_bridge = idle_after_bridge
        self._positions_after_submit = positions_after_submit
        self.bridged: List[float] = []
        self.submitted: List[List[OnchainOrder]] = []

    def idle_assets(self): return self._idle
    def pending_redeem_assets(self): return self._pending
    def nav(self): return self._nav
    def core_available(self): return self._core
    def positions(self): return dict(self._positions)
    def prices(self): return dict(self._prices)

    def bridge_from_core(self, amount):
        self.bridged.append(amount)
        if self._idle_after_bridge is not None:
            self._idle = self._idle_after_bridge
        return "0xbridge"

    def submit_basket(self, orders):
        self.submitted.append(orders)
        if self._positions_after_submit is not None:
            self._positions = dict(self._positions_after_submit)
        return "0xsubmit"


def _bot(client, **kw):
    return KeeperBot(
        client=client, target_weights=EQUAL, sz_decimals=SZ, asset_ids=IDS, **kw
    )


# ── pure helpers ────────────────────────────────────────────────
def test_target_sizes_from_weights_long_and_short():
    long = target_sizes_from_weights(EQUAL, PRICES, total_notional=400, side="long")
    assert long == {c: 10.0 for c in EQUAL}          # 400*0.25/10 = 10
    short = target_sizes_from_weights(EQUAL, PRICES, total_notional=400, side="short")
    assert short == {c: -10.0 for c in EQUAL}


def test_target_sizes_rejects_bad_inputs():
    with pytest.raises(ValueError):
        target_sizes_from_weights(EQUAL, PRICES, total_notional=400, side="sideways")
    with pytest.raises(ValueError):
        target_sizes_from_weights(EQUAL, {"A": 0.0}, total_notional=100)
    with pytest.raises(ValueError):
        target_sizes_from_weights(EQUAL, PRICES, total_notional=-1)


def test_gross_notional_counts_shorts():
    assert gross_notional({"A": 5, "B": -5}, PRICES) == 100.0


# ── liquidity job ───────────────────────────────────────────────
def test_liquidity_no_bridge_when_buffer_covered():
    client = FakeClient(idle=1000, pending=100, nav=10_000, core=5000)
    res = _bot(client).tick().liquidity
    assert res.bridged == 0.0 and res.submitted is False and res.verified is True
    assert client.bridged == []


def test_liquidity_dry_run_plans_but_does_not_send():
    client = FakeClient(idle=100, pending=600, nav=10_000, core=5000)
    res = _bot(client, dry_run=True).tick().liquidity
    assert res.bridged == 1000.0 and res.submitted is False
    assert client.bridged == []


def test_liquidity_executes_and_verifies():
    # deficit 1000; idle rises to 1100 after bridge settles -> verified
    client = FakeClient(idle=100, pending=600, nav=10_000, core=5000, idle_after_bridge=1100)
    res = _bot(client, dry_run=False).tick().liquidity
    assert res.submitted is True and res.verified is True
    assert client.bridged == [1000.0] and res.tx == "0xbridge"


def test_liquidity_flags_unverified_when_idle_does_not_rise():
    client = FakeClient(idle=100, pending=600, nav=10_000, core=5000, idle_after_bridge=100)
    res = _bot(client, dry_run=False).tick().liquidity
    assert res.submitted is True and res.verified is False
    assert "UNVERIFIED" in res.note


def test_liquidity_reports_shortfall():
    client = FakeClient(idle=100, pending=600, nav=10_000, core=300)
    res = _bot(client, dry_run=False).tick().liquidity
    assert res.shortfall == pytest.approx(700.0)


# ── rebalance job ───────────────────────────────────────────────
def _balanced_positions() -> Dict[str, float]:
    return {c: 10.0 for c in EQUAL}


def test_rebalance_skipped_within_threshold():
    client = FakeClient(positions=_balanced_positions(), prices=PRICES)
    res = _bot(client).tick().rebalance
    assert res.triggered is False and client.submitted == []


def test_rebalance_triggers_on_drift_dry_run():
    # A is heavy, D is light -> drift well over the default 2% threshold
    drifted = {"A": 20.0, "B": 10.0, "C": 10.0, "D": 2.0}
    client = FakeClient(positions=drifted, prices=PRICES)
    res = _bot(client, dry_run=True).tick().rebalance
    assert res.triggered is True and res.submitted is False
    assert res.orders and client.submitted == []


def test_rebalance_executes_and_verifies():
    drifted = {"A": 20.0, "B": 10.0, "C": 10.0, "D": 2.0}
    # after submit, the book is balanced -> drift clears -> verified
    client = FakeClient(
        positions=drifted, prices=PRICES,
        positions_after_submit=_balanced_positions(),
    )
    res = _bot(client, dry_run=False).tick().rebalance
    assert res.submitted is True and res.verified is True
    assert len(client.submitted) == 1 and res.tx == "0xsubmit"


def test_rebalance_unverified_when_drift_persists():
    drifted = {"A": 20.0, "B": 10.0, "C": 10.0, "D": 2.0}
    client = FakeClient(
        positions=drifted, prices=PRICES,
        positions_after_submit=drifted,   # nothing changed -> still drifted
    )
    res = _bot(client, dry_run=False, config=KeeperConfig()).tick().rebalance
    assert res.submitted is True and res.verified is False
    assert "UNVERIFIED" in res.note


# ── fail-closed gate ────────────────────────────────────────────
def test_bot_refuses_to_act_on_contradictory_reads():
    # idle (5,000) exceeds NAV (1,000) -> a contradiction; the bot must not act
    client = FakeClient(idle=5000, pending=0, nav=1000, core=5000)
    report = _bot(client, dry_run=False).tick()
    assert report.blockers  # gate fired
    assert report.liquidity.submitted is False and report.rebalance.submitted is False
    assert client.bridged == [] and client.submitted == []
    assert "gate blocked" in report.liquidity.note
