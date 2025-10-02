"""High-level API for bolig analyse (score, UI mapping)."""
from __future__ import annotations

from .contracts import (
    CalculatedMetrics,
    DecisionFacts,
    DecisionResult,
    DecisionVerdict,
    InputContract,
    KeyFigure,
    build_calculated_metrics,
)
from .risk import calc_risk_score, calc_total_score
from .scoring import (
    beregn_score_og_dom,
    farge_for_break_even_gap,
    farge_for_cashflow,
    farge_for_roe,
)
from .ui import build_decision_result, map_decision_to_ui

__all__ = [
    "CalculatedMetrics",
    "DecisionFacts",
    "DecisionResult",
    "DecisionVerdict",
    "InputContract",
    "KeyFigure",
    "build_calculated_metrics",
    "beregn_score_og_dom",
    "farge_for_break_even_gap",
    "farge_for_cashflow",
    "farge_for_roe",
    "calc_risk_score",
    "calc_total_score",
    "build_decision_result",
    "map_decision_to_ui",
]
