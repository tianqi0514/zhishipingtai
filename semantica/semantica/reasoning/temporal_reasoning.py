"""
Reasoning-layer re-export for deterministic temporal reasoning.

The canonical implementation lives in ``semantica.kg.temporal_reasoning``.
"""

from ..kg.temporal_reasoning import IntervalRelation, TemporalInterval, TemporalReasoningEngine

__all__ = ["TemporalInterval", "IntervalRelation", "TemporalReasoningEngine"]
