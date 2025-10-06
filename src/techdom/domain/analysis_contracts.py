"""Backward-compatible shim som videresender til `techdom.domain.analysis`."""
from __future__ import annotations

from techdom.domain.analysis import (
    CalculatedMetrics,
    DecisionFacts,
    DecisionResult,
    DecisionVerdict,
    InputContract,
    KeyFigure,
    build_calculated_metrics,
    build_decision_result,
    calc_risk_score,
    calc_total_score,
    compute_scores,
    farge_for_break_even_gap,
    farge_for_cashflow,
    farge_for_roe,
    ScoreSummary,
    map_decision_to_ui,
)

__all__ = [
    "InputContract",
    "CalculatedMetrics",
    "DecisionFacts",
    "DecisionVerdict",
    "KeyFigure",
    "DecisionResult",
    "build_calculated_metrics",
    "build_decision_result",
    "map_decision_to_ui",
    "farge_for_cashflow",
    "farge_for_roe",
    "farge_for_break_even_gap",
    "calc_risk_score",
    "calc_total_score",
    "compute_scores",
    "ScoreSummary",
]
