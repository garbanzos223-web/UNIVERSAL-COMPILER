"""Compilation optimization: profiles, plan changes, measurement and advice.

The optimizer is opt-in: without a profile the build behaves exactly as it did
before. With a profile it adjusts the commands of the existing build plan,
measures the result and reports only what it measured.
"""

from __future__ import annotations

from .measure import ComparisonReport, Measurement, compare, record_optimization
from .plan import (
    OptimizationPlan,
    OptimizationRequest,
    apply_optimization,
    build_optimization_plan,
)
from .profiles import (
    ADAPTER_FAMILY,
    PROFILE_KEYS,
    PROFILES,
    describe_profiles,
    get_profile,
    profile_keys,
)
from .recommend import Recommendation, recommendations

__all__ = [
    "ADAPTER_FAMILY",
    "PROFILES",
    "PROFILE_KEYS",
    "ComparisonReport",
    "Measurement",
    "OptimizationPlan",
    "OptimizationRequest",
    "Recommendation",
    "apply_optimization",
    "build_optimization_plan",
    "compare",
    "describe_profiles",
    "get_profile",
    "profile_keys",
    "record_optimization",
    "recommendations",
]
