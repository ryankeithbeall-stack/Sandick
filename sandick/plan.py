"""Serialize an AllocationPlan to a reviewable JSON artifact.

The saved plan is the hand-off between *planning* (this dry-run tool) and
*execution* (a future `--execute` step): an admin reviews the JSON, then
execution consumes exactly those orders. Including a schema version lets the
executor refuse anything it doesn't understand.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from .allocator import AllocationPlan

PLAN_SCHEMA_VERSION = 1


def plan_to_dict(plan: AllocationPlan) -> Dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "basket": plan.basket.name,
        "dex": plan.basket.dex,
        "side": plan.side,
        "capital": plan.capital,
        "default_leverage": plan.leverage,
        "gross_notional": plan.gross_notional,
        "deployed_margin": plan.deployed_margin,
        "residual_cash": plan.residual_cash,
        "orders": [
            {
                "coin": o.asset.coin,
                "ticker": o.asset.ticker,
                "company": o.asset.company,
                "side": o.side,
                "price": o.price,
                "leverage": o.leverage,
                "size": o.size,
                "sz_decimals": o.asset.sz_decimals,
                "notional": o.notional,
                "margin": o.margin,
                "target_weight": o.target_weight,
                "actual_weight": o.actual_weight,
            }
            for o in plan.orders
        ],
    }


def write_plan(plan: AllocationPlan, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan_to_dict(plan), fh, indent=2)
        fh.write("\n")
