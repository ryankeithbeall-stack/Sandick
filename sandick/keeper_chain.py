"""Live chain adapter for the keeper bot.

``keeper_bot.KeeperBot`` drives the vault through the ``KeeperClient`` seam;
this module provides the concrete implementation against a deployed
``BasketVault`` on HyperEVM:

* **Reads** (idle USDC, queued-redemption value, NAV) come from the vault's own
  view functions. ``core_available`` reads the HyperCore ``accountMarginSummary``
  precompile directly (via the reader's configured address + perp-dex index) so
  the keeper sizes ``bridgeFromCore`` against *free* margin, not gross equity.
* **Positions & mark prices** come from an injected :class:`MarketData` source
  (the HyperCore read precompiles don't expose the book; the Hyperliquid info
  API does — see :class:`HyperliquidMarketData`).
* **Writes** (``bridgeFromCore``, ``submitBasket``) are signed by the manager key
  and sent through the injected web3 client.

The module imports cleanly **without** web3/eth-account installed — those are
imported lazily only inside :meth:`Web3KeeperClient.from_endpoint` and the live
market-data source. Everything else operates on injected objects, so the unit
tests exercise the real read/write/encoding logic against a fake web3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from typing import Dict, List, Optional, Protocol

from .onchain import OnchainOrder
from .safety import require_tx_allowed

# ── Minimal ABIs (only the functions the keeper touches) ────────────────────
_ORDER_COMPONENTS = [
    {"name": "assetId", "type": "uint32"},
    {"name": "isBuy", "type": "bool"},
    {"name": "limitPx", "type": "uint64"},
    {"name": "sz", "type": "uint64"},
    {"name": "reduceOnly", "type": "bool"},
]

VAULT_ABI = [
    {"type": "function", "stateMutability": "view", "name": "totalAssets", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "reservedAssets", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "totalPendingRedeemShares", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "convertToAssets", "inputs": [{"type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "reader", "inputs": [], "outputs": [{"type": "address"}]},
    {"type": "function", "stateMutability": "view", "name": "manager", "inputs": [], "outputs": [{"type": "address"}]},
    {"type": "function", "stateMutability": "nonpayable", "name": "bridgeFromCore", "inputs": [{"type": "uint256"}], "outputs": []},
    {"type": "function", "stateMutability": "nonpayable", "name": "submitBasket", "inputs": [{"type": "tuple[]", "name": "orders", "components": _ORDER_COMPONENTS}], "outputs": []},
]

ERC20_ABI = [
    {"type": "function", "stateMutability": "view", "name": "balanceOf", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "stateMutability": "view", "name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}]},
]

READER_ABI = [
    {"type": "function", "stateMutability": "view", "name": "perpDexIndex", "inputs": [], "outputs": [{"type": "uint32"}]},
    {"type": "function", "stateMutability": "view", "name": "marginSummaryPrecompile", "inputs": [], "outputs": [{"type": "address"}]},
]


# ── Pure ABI coding for the one precompile read ─────────────────────────────
# accountMarginSummary takes abi.encode(uint32 perpDexIndex, address user) and
# returns abi.encode(int64 accountValue, uint64 marginUsed, uint64 ntlPos,
# int64 rawUsd). Both shapes are fixed words, so we (de)code them in pure Python
# and keep the module free of an eth-abi dependency.
def encode_margin_query(perp_dex_index: int, address: str) -> bytes:
    """ABI-encode ``(uint32 perpDexIndex, address user)`` as two 32-byte words."""
    word0 = int(perp_dex_index).to_bytes(32, "big")
    word1 = int(address, 16).to_bytes(32, "big")
    return word0 + word1


def decode_margin_summary(data: bytes) -> tuple[int, int]:
    """Decode ``accountValue`` (int64) and ``marginUsed`` (uint64) from the result.

    Returns ``(account_value, margin_used)``; both are HyperCore 6-decimal USDC
    integer units (the same decimals as the vault's USDC asset).
    """
    if len(data) < 128:
        raise ValueError("margin summary result too short")
    account_value = int.from_bytes(data[0:32], "big", signed=True)
    margin_used = int.from_bytes(data[32:64], "big")
    return account_value, margin_used


# ── Market data (positions + mark prices) ───────────────────────────────────
class MarketData(Protocol):
    """Source of the live book the keeper rebalances against."""

    def positions(self) -> Dict[str, float]: ...   # signed size per coin
    def prices(self) -> Dict[str, float]: ...       # mark price per coin


@dataclass(frozen=True)
class StaticMarketData:
    """Fixed positions/prices — for tests, back-tests, or manual overrides."""

    _positions: Dict[str, float]
    _prices: Dict[str, float]

    def positions(self) -> Dict[str, float]:
        return dict(self._positions)

    def prices(self) -> Dict[str, float]:
        return dict(self._prices)


@dataclass
class HyperliquidMarketData:
    """Live positions/prices from the Hyperliquid info API for a HIP-3 dex.

    Prices reuse :func:`sandick.prices.fetch_live_prices`. Positions come from the
    vault's clearinghouse state on the dex.

    NOTE: the ``user_state`` payload shape for a HIP-3 builder dex must be
    confirmed on testnet (the ``dex`` argument + ``assetPositions`` layout) — this
    is a live-only path and is not covered by the offline tests.
    """

    vault_address: str
    coins: List[str]
    dex: str = ""
    mainnet: bool = False

    def prices(self) -> Dict[str, float]:
        from .prices import fetch_live_prices

        return fetch_live_prices(self.coins, dex=self.dex, mainnet=self.mainnet)

    def positions(self) -> Dict[str, float]:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        base = constants.MAINNET_API_URL if self.mainnet else constants.TESTNET_API_URL
        info = Info(base, skip_ws=True)
        state = info.user_state(self.vault_address, dex=self.dex)
        out: Dict[str, float] = {}
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            coin = pos.get("coin")
            if coin is not None:
                out[coin] = float(pos.get("szi", 0) or 0)
        return out


# ── The adapter ─────────────────────────────────────────────────────────────
@dataclass
class Web3KeeperClient:
    """Concrete :class:`keeper_bot.KeeperClient` over a deployed BasketVault.

    Args:
        w3: a connected web3 client (or a compatible fake in tests).
        vault_address: the deployed BasketVault address.
        usdc_address: the vault's underlying USDC token.
        account: signer for writes (an eth-account ``LocalAccount`` or compatible);
            ``None`` makes the client read-only and writes raise.
        asset_decimals: USDC decimals (6); also the units the margin summary uses.
        market_data: positions/prices source (required for the rebalance job).
        gas: optional fixed gas limit for write txs (else web3 estimates).
    """

    w3: object
    vault_address: str
    usdc_address: str
    account: object = None
    asset_decimals: int = 6
    market_data: Optional[MarketData] = None
    gas: Optional[int] = None
    _cache: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._vault = self.w3.eth.contract(address=self.vault_address, abi=VAULT_ABI)
        self._usdc = self.w3.eth.contract(address=self.usdc_address, abi=ERC20_ABI)
        self._scale = 10 ** self.asset_decimals

    @classmethod
    def from_endpoint(
        cls,
        rpc_url: str,
        vault_address: str,
        usdc_address: str,
        *,
        private_key: Optional[str] = None,
        market_data: Optional[MarketData] = None,
        asset_decimals: Optional[int] = None,
        gas: Optional[int] = None,
    ) -> "Web3KeeperClient":
        """Build a live client from an RPC URL (imports web3 / eth-account lazily)."""
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(rpc_url))
        vault_address = Web3.to_checksum_address(vault_address)
        usdc_address = Web3.to_checksum_address(usdc_address)

        account = None
        if private_key:
            from eth_account import Account

            account = Account.from_key(private_key)

        if asset_decimals is None:
            usdc = w3.eth.contract(address=usdc_address, abi=ERC20_ABI)
            asset_decimals = usdc.functions.decimals().call()

        return cls(
            w3=w3, vault_address=vault_address, usdc_address=usdc_address,
            account=account, asset_decimals=asset_decimals,
            market_data=market_data, gas=gas,
        )

    # ---- unit conversion ----
    def _to_float(self, raw: int) -> float:
        return raw / self._scale

    def _to_raw(self, amount: float) -> int:
        return int((Decimal(str(amount)) * self._scale).to_integral_value(rounding=ROUND_FLOOR))

    # ---- reads ----
    def idle_assets(self) -> float:
        bal = self._usdc.functions.balanceOf(self.vault_address).call()
        reserved = self._vault.functions.reservedAssets().call()
        return self._to_float(max(0, bal - reserved))

    def pending_redeem_assets(self) -> float:
        shares = self._vault.functions.totalPendingRedeemShares().call()
        if shares == 0:
            return 0.0
        return self._to_float(self._vault.functions.convertToAssets(shares).call())

    def nav(self) -> float:
        return self._to_float(self._vault.functions.totalAssets().call())

    def core_available(self) -> float:
        """Free (withdrawable) margin on HyperCore: accountValue − marginUsed.

        Margin backing open positions can't be pulled, so the keeper bridges only
        against the free portion. A never-initialized Core account makes the
        precompile revert — treated as 0 available (seed the account first)."""
        try:
            dex_index, precompile = self._margin_meta()
            data = self.w3.eth.call({
                "to": precompile,
                "data": "0x" + encode_margin_query(dex_index, self.vault_address).hex(),
            })
            account_value, margin_used = decode_margin_summary(bytes(data))
        except Exception:
            return 0.0
        free = account_value - margin_used
        return self._to_float(free) if free > 0 else 0.0

    def _margin_meta(self) -> tuple[int, str]:
        if "margin_meta" not in self._cache:
            reader_addr = self._vault.functions.reader().call()
            reader = self.w3.eth.contract(address=reader_addr, abi=READER_ABI)
            self._cache["margin_meta"] = (
                reader.functions.perpDexIndex().call(),
                reader.functions.marginSummaryPrecompile().call(),
            )
        return self._cache["margin_meta"]

    def positions(self) -> Dict[str, float]:
        return dict(self._market().positions())

    def prices(self) -> Dict[str, float]:
        return dict(self._market().prices())

    def _market(self) -> MarketData:
        if self.market_data is None:
            raise RuntimeError("market_data is not configured (positions/prices unavailable)")
        return self.market_data

    # ---- writes (manager-signed) ----
    def bridge_from_core(self, amount: float) -> str:
        return self._send(self._vault.functions.bridgeFromCore(self._to_raw(amount)))

    def submit_basket(self, orders: List[OnchainOrder]) -> str:
        tuples = [o.as_tuple() for o in orders]
        return self._send(self._vault.functions.submitBasket(tuples))

    def _send(self, fn) -> str:
        if self.account is None:
            raise RuntimeError("no signing account configured; client is read-only")
        require_tx_allowed("keeper bridge/submit")
        addr = self.account.address
        tx = fn.build_transaction({
            "from": addr,
            "nonce": self.w3.eth.get_transaction_count(addr),
        })
        if self.gas is not None:
            tx["gas"] = self.gas
        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:  # web3 < 7 exposed rawTransaction
            raw = signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        return tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
