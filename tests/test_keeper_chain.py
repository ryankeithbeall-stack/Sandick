"""Tests for the live chain adapter (keeper_chain.py).

A FakeW3 mocks exactly the web3 surface the adapter uses — contract reads,
``eth.call`` for the precompile, and the sign/send write path — so the real
read/write/encoding logic runs against canned chain state, no node required.
"""

import pytest

from sandick.keeper_bot import KeeperBot, run_loop
from sandick.keeper_chain import (
    StaticMarketData,
    Web3KeeperClient,
    decode_margin_summary,
    encode_margin_query,
)
from sandick.onchain import OnchainOrder

VAULT = "0x000000000000000000000000000000000000aaaa"
USDC = "0x000000000000000000000000000000000000bbbb"
READER = "0x000000000000000000000000000000000000cccc"
PRECOMPILE = "0x000000000000000000000000000000000000080F"


# ── fake web3 ───────────────────────────────────────────────────
class FakeFn:
    def __init__(self, name, args, address, returns, log):
        self.name, self.args, self.address, self.returns, self.log = name, args, address, returns, log

    def call(self):
        self.log.append(("call", self.address, self.name, self.args))
        val = self.returns[self.name]
        return val(*self.args) if callable(val) else val

    def build_transaction(self, tx):
        out = dict(tx)
        out.update(to=self.address, fn=self.name, args=self.args)
        return out


class FakeFunctions:
    def __init__(self, address, returns, log):
        self._address, self._returns, self._log = address, returns, log

    def __getattr__(self, name):
        def make(*args):
            return FakeFn(name, args, self._address, self._returns, self._log)
        return make


class FakeContract:
    def __init__(self, address, returns, log):
        self.functions = FakeFunctions(address, returns, log)


class FakeSigned:
    raw_transaction = b"\x02signedrawtx"


class FakeEth:
    def __init__(self, returns_by_addr, precompile_bytes, log):
        self._returns_by_addr = returns_by_addr
        self._precompile_bytes = precompile_bytes
        self._log = log
        self.chain_id = 998
        self.sent = []

    def contract(self, address, abi):
        return FakeContract(address, self._returns_by_addr.get(address, {}), self._log)

    def call(self, tx):
        self._log.append(("eth_call", tx))
        if isinstance(self._precompile_bytes, Exception):
            raise self._precompile_bytes
        return self._precompile_bytes

    def get_transaction_count(self, addr):
        return 7

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        return b"\xab\xcd"


class FakeW3:
    def __init__(self, returns_by_addr, precompile_bytes=b"", log=None):
        self.log = [] if log is None else log
        self.eth = FakeEth(returns_by_addr, precompile_bytes, self.log)


class FakeAccount:
    address = "0x000000000000000000000000000000000000d00d"

    def __init__(self):
        self.signed = []

    def sign_transaction(self, tx):
        self.signed.append(tx)
        return FakeSigned()


def _margin_result(account_value, margin_used, ntl_pos=0, raw_usd=0):
    return (
        account_value.to_bytes(32, "big", signed=True)
        + margin_used.to_bytes(32, "big")
        + ntl_pos.to_bytes(32, "big")
        + raw_usd.to_bytes(32, "big", signed=True)
    )


def _client(*, returns=None, precompile=b"", account=None, market=None, **kw):
    vault_returns = {
        "totalAssets": 250_000_000_000,          # 250,000 USDC (6dp)
        "reservedAssets": 0,
        "totalPendingRedeemShares": 0,
        "convertToAssets": lambda shares: shares,  # 1:1 for simplicity
        "reader": READER,
        "manager": FakeAccount.address,
    }
    if returns:
        vault_returns.update(returns)
    w3 = FakeW3(
        {
            VAULT: vault_returns,
            USDC: {"balanceOf": 12_000_000_000, "decimals": 6},   # 12,000 idle
            READER: {"perpDexIndex": 1, "marginSummaryPrecompile": PRECOMPILE},
        },
        precompile_bytes=precompile,
    )
    return Web3KeeperClient(
        w3=w3, vault_address=VAULT, usdc_address=USDC,
        account=account, market_data=market, **kw,
    )


# ── pure ABI helpers ────────────────────────────────────────────
def test_encode_margin_query_layout():
    enc = encode_margin_query(1, VAULT)
    assert len(enc) == 64
    assert int.from_bytes(enc[0:32], "big") == 1
    assert ("%040x" % int(VAULT, 16)) in enc.hex()


def test_decode_margin_summary_roundtrip():
    data = _margin_result(-5, 3)            # negative account value, signed
    assert decode_margin_summary(data) == (-5, 3)
    with pytest.raises(ValueError):
        decode_margin_summary(b"\x00" * 64)  # too short


# ── reads ───────────────────────────────────────────────────────
def test_idle_assets_nets_reserved():
    c = _client(returns={"reservedAssets": 2_000_000_000})  # 2,000 reserved
    assert c.idle_assets() == pytest.approx(10_000.0)       # 12,000 - 2,000


def test_idle_assets_floors_at_zero():
    c = _client(returns={"reservedAssets": 99_000_000_000})
    assert c.idle_assets() == 0.0


def test_nav_scales_decimals():
    assert _client().nav() == pytest.approx(250_000.0)


def test_pending_redeem_zero_short_circuits():
    c = _client(returns={"totalPendingRedeemShares": 0})
    assert c.pending_redeem_assets() == 0.0
    # convertToAssets must NOT have been called
    assert not any(e[2] == "convertToAssets" for e in c.w3.log if e[0] == "call")


def test_pending_redeem_uses_convert_to_assets():
    c = _client(returns={"totalPendingRedeemShares": 4_000_000_000})  # ->4,000 (1:1)
    assert c.pending_redeem_assets() == pytest.approx(4_000.0)


def test_core_available_free_margin():
    # accountValue 100,000 - marginUsed 30,000 = 70,000 free
    c = _client(precompile=_margin_result(100_000_000_000, 30_000_000_000))
    assert c.core_available() == pytest.approx(70_000.0)


def test_core_available_clamps_when_fully_used():
    c = _client(precompile=_margin_result(30_000_000_000, 30_000_000_000))
    assert c.core_available() == 0.0


def test_core_available_zero_on_precompile_revert():
    c = _client(precompile=RuntimeError("uninitialized core account"))
    assert c.core_available() == 0.0


def test_core_available_caches_reader_meta():
    c = _client(precompile=_margin_result(50_000_000_000, 0))
    c.core_available()
    c.core_available()
    reader_reads = [e for e in c.w3.log if e[0] == "call" and e[2] == "perpDexIndex"]
    assert len(reader_reads) == 1  # reader meta fetched once, then cached


# ── market data ─────────────────────────────────────────────────
def test_positions_and_prices_from_market_data():
    md = StaticMarketData({"A": 5.0, "B": -2.0}, {"A": 10.0, "B": 20.0})
    c = _client(market=md)
    assert c.positions() == {"A": 5.0, "B": -2.0}
    assert c.prices() == {"A": 10.0, "B": 20.0}


def test_positions_without_market_data_raises():
    with pytest.raises(RuntimeError, match="market_data"):
        _client().positions()


# ── writes ──────────────────────────────────────────────────────
def test_bridge_from_core_converts_and_sends(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TX", "1")
    acct = FakeAccount()
    c = _client(account=acct)
    tx_hash = c.bridge_from_core(1234.56)
    assert tx_hash == "abcd"
    built = acct.signed[0]
    assert built["fn"] == "bridgeFromCore"
    assert built["args"] == (1_234_560_000,)            # 1234.56 * 1e6, floored
    assert built["from"] == acct.address and built["nonce"] == 7


def test_bridge_from_core_floors_sub_unit(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TX", "1")
    acct = FakeAccount()
    c = _client(account=acct)
    c.bridge_from_core(0.0000019)                        # below 1e-6 -> floors to 1
    assert acct.signed[0]["args"] == (1,)


def test_submit_basket_encodes_order_tuples(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TX", "1")
    acct = FakeAccount()
    c = _client(account=acct, gas=500_000)
    orders = [
        OnchainOrder(asset_id=110001, is_buy=True, limit_px=5_000_000_000, sz=20_000_000_000, reduce_only=False),
        OnchainOrder(asset_id=110002, is_buy=False, limit_px=8_000_000_000, sz=10_000_000_000, reduce_only=True),
    ]
    c.submit_basket(orders)
    built = acct.signed[0]
    assert built["fn"] == "submitBasket"
    assert built["gas"] == 500_000
    assert built["args"] == ([
        (110001, True, 5_000_000_000, 20_000_000_000, False),
        (110002, False, 8_000_000_000, 10_000_000_000, True),
    ],)


def test_writes_require_account():
    c = _client(account=None)
    with pytest.raises(RuntimeError, match="read-only"):
        c.bridge_from_core(100.0)


def test_send_requires_allow_live_tx_env(monkeypatch):
    # account present but the hard env gate is not set -> refuse to broadcast
    monkeypatch.delenv("ALLOW_LIVE_TX", raising=False)
    c = _client(account=FakeAccount())
    with pytest.raises(Exception, match="ALLOW_LIVE_TX"):
        c.bridge_from_core(100.0)
    # opting in lets it through
    monkeypatch.setenv("ALLOW_LIVE_TX", "1")
    assert c.bridge_from_core(100.0) == "abcd"


# ── end-to-end with the bot ─────────────────────────────────────
def test_bot_dry_run_plans_bridge_via_adapter():
    # pending 5,000 (1:1) + 5% of 250,000 buffer (12,500) = 17,500 need; idle
    # 12,000 -> deficit 5,500; core has 70,000 free -> bridge 5,500 planned.
    c = _client(
        returns={"totalPendingRedeemShares": 5_000_000_000},
        precompile=_margin_result(100_000_000_000, 30_000_000_000),
        market=StaticMarketData({"A": 10.0, "B": 10.0}, {"A": 10.0, "B": 10.0}),  # balanced
    )
    bot = KeeperBot(
        client=c, target_weights={"A": 0.5, "B": 0.5},
        sz_decimals={"A": 2, "B": 2}, asset_ids={"A": 110001, "B": 110002},
        dry_run=True,
    )
    report = bot.tick()
    assert report.liquidity.bridged == pytest.approx(5_500.0)
    assert report.liquidity.submitted is False
    assert report.rebalance.triggered is False  # balanced book -> no rebalance


# ── loop runner ─────────────────────────────────────────────────
class CountingBot:
    def __init__(self):
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        return f"report-{self.ticks}"


def test_run_loop_bounded_and_sleeps_between_ticks():
    bot = CountingBot()
    naps = []
    seen = []
    reports = run_loop(bot, interval=30, max_ticks=3, sleep=naps.append, on_report=seen.append)
    assert reports == ["report-1", "report-2", "report-3"]
    assert seen == reports
    assert naps == [30, 30]          # sleeps BETWEEN ticks, not after the last
    assert bot.ticks == 3


def test_run_loop_rejects_negative_interval():
    with pytest.raises(ValueError):
        run_loop(CountingBot(), interval=-1, max_ticks=1)
