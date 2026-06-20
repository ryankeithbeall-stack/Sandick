"""Sandick — equal-weighted HIP-3 basket vault tooling."""

from .allocator import (
    AllocationPlan,
    PlannedOrder,
    build_equal_weight_plan,
    build_plan,
    round_size,
)
from .basket import Basket, BasketAsset
from .weights import resolve_weights

__all__ = [
    "AllocationPlan",
    "PlannedOrder",
    "build_plan",
    "build_equal_weight_plan",
    "round_size",
    "Basket",
    "BasketAsset",
    "resolve_weights",
]

__version__ = "0.1.0"
