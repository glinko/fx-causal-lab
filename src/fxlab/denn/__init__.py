"""Deterministic DENN research primitives; graph training is intentionally deferred."""

from .pipeline import age_decay, build_denn_baseline, exponential_memory
from .spectral import build_spectral_baseline
from .stability import build_spectral_stability
from .grouped import build_grouped
from .grouped_tier_a import build_grouped_tier_a
from .memory_ablation import build_memory_ablation
from .block_pca import build_block_pca
from .state_vector import build_state_vector
from .timing_audit import build_timing_audit

__all__ = [
    "age_decay", "build_block_pca", "build_denn_baseline", "build_grouped", "build_grouped_tier_a", "build_memory_ablation", "build_spectral_baseline",
    "build_spectral_stability",
    "build_state_vector", "build_timing_audit",
    "exponential_memory",
]
