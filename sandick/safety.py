"""Hard broadcast kill-switch shared by every live-transaction path.

A single, explicit, out-of-band gate that EVERY chain-mutating action must pass
before it can broadcast: the ``ALLOW_LIVE_TX`` environment variable must equal
``"1"``. This is defense-in-depth on top of the per-command ``--execute`` /
``confirm`` flags — so an accidental ``--execute`` (or a stray confirm) with a
key present can never silently transmit. Adapted from the Wren build's
``ALLOW_TESTNET_TX=1`` pattern.

It only ever makes broadcasting *harder*, never easier, so it cannot weaken the
on-chain "manager can never move funds out" invariant.
"""

from __future__ import annotations

import os

ALLOW_LIVE_TX_ENV = "ALLOW_LIVE_TX"


class LiveTxNotAllowed(RuntimeError):
    """Raised when a live broadcast is attempted without the env opt-in."""


def tx_allowed() -> bool:
    """True only when ``ALLOW_LIVE_TX=1`` is set in the environment."""
    return os.environ.get(ALLOW_LIVE_TX_ENV) == "1"


def require_tx_allowed(context: str = "") -> None:
    """Raise :class:`LiveTxNotAllowed` unless live transactions are enabled.

    Call this immediately before any signed broadcast. ``context`` is folded into
    the error message to say which path tripped the gate.
    """
    if not tx_allowed():
        where = f" ({context})" if context else ""
        raise LiveTxNotAllowed(
            f"Refusing to broadcast a live transaction{where}: set "
            f"{ALLOW_LIVE_TX_ENV}=1 to explicitly enable live transactions."
        )
