"""Domain-facing helpers for running the full property analysis workflow.

The Streamlit UI historically orchestrated this flow inline. We expose it here so
other entrypoints (API, workers, tests) can reuse the exact logic without pulling
in UI-specific state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from pydantic import ValidationError

from techdom.domain.analysis_contracts import (
    CalculatedMetrics,
    DecisionResult,
    InputContract,
    build_calculated_metrics,
    build_decision_result,
    map_decision_to_ui,
)
from techdom.processing.ai import ai_explain
from techdom.processing.compute import compute_metrics

DEFAULT_EQUITY_PCT = 0.15


def as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.replace("\u00a0", " ").replace(" ", "").replace(",", "")
        try:
            return int(float(text))
        except Exception:
            return default
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
        try:
            return float(text)
        except Exception:
            return default
    return default


def as_opt_float(value: Any) -> Optional[float]:
    candidate = as_float(value, default=float("nan"))
    return None if candidate != candidate else candidate  # NaN check


def default_equity(price: Any) -> int:
    price_float = as_float(price, 0.0)
    if price_float <= 0:
        return 0
    return int(round(price_float * DEFAULT_EQUITY_PCT))


def input_contract_from_params(params: Mapping[str, Any]) -> InputContract:
    return InputContract(
        kjopesum=as_float(params.get("price", 0)),
        egenkapital=as_float(params.get("equity", 0)),
        rente_pct_pa=as_float(params.get("interest", 0.0)),
        lanetid_ar=as_int(params.get("term_years", 30), 30),
        brutto_leie_mnd=as_float(params.get("rent", 0)),
        felleskost_mnd=as_float(params.get("hoa", 0)),
        vedlikehold_pct_av_leie=as_float(params.get("maint_pct", 0.0)),
        andre_kost_mnd=as_float(params.get("other_costs", 0)),
    )


@dataclass(frozen=True)
class AnalysisDecisionContext:
    tg2_items: Sequence[str] = ()
    tg3_items: Sequence[str] = ()
    tg_data_available: bool = False


@dataclass
class AnalysisResult:
    metrics: Dict[str, Any]
    calculated_metrics: Optional[CalculatedMetrics]
    decision_result: Optional[DecisionResult]
    decision_ui: Dict[str, Any]
    ai_text: str


def normalise_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    """Public helper that mirrors the historical parameter coercion logic."""
    return _normalised_params(params)


def _normalised_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "price": as_int(params.get("price")),
        "equity": as_int(params.get("equity")),
        "interest": as_float(params.get("interest")),
        "term_years": as_int(params.get("term_years"), 30),
        "rent": as_int(params.get("rent")),
        "hoa": as_int(params.get("hoa")),
        "maint_pct": as_float(params.get("maint_pct")),
        "vacancy_pct": as_float(params.get("vacancy_pct")),
        "other_costs": as_int(params.get("other_costs")),
    }


def compute_analysis(
    params: Mapping[str, Any],
    decision_context: Optional[AnalysisDecisionContext] = None,
) -> AnalysisResult:
    normalised = _normalised_params(params)
    metrics = compute_metrics(
        normalised["price"],
        normalised["equity"],
        normalised["interest"],
        normalised["term_years"],
        normalised["rent"],
        normalised["hoa"],
        normalised["maint_pct"],
        normalised["vacancy_pct"],
        normalised["other_costs"],
    )

    calculated_metrics: Optional[CalculatedMetrics]
    decision_result: Optional[DecisionResult]
    decision_ui: Dict[str, Any]

    try:
        contract = input_contract_from_params(params)
        calculated_metrics = build_calculated_metrics(contract)
        ctx = decision_context or AnalysisDecisionContext()
        decision_result = build_decision_result(
            contract,
            calculated_metrics,
            tg2_items=list(ctx.tg2_items),
            tg3_items=list(ctx.tg3_items),
            tg_data_available=ctx.tg_data_available,
        )
        decision_ui = map_decision_to_ui(decision_result)
    except ValidationError:
        calculated_metrics = None
        decision_result = None
        decision_ui = {}

    ai_inputs = {
        "price": normalised["price"],
        "equity": normalised["equity"],
        "interest": normalised["interest"],
        "term_years": normalised["term_years"],
        "rent": normalised["rent"],
        "hoa": normalised["hoa"],
    }
    ai_text = ai_explain(ai_inputs, metrics)

    return AnalysisResult(
        metrics=metrics,
        calculated_metrics=calculated_metrics,
        decision_result=decision_result,
        decision_ui=decision_ui,
        ai_text=ai_text,
    )


__all__ = [
    "DEFAULT_EQUITY_PCT",
    "AnalysisDecisionContext",
    "AnalysisResult",
    "as_str",
    "as_int",
    "as_float",
    "as_opt_float",
    "compute_analysis",
    "normalise_params",
    "default_equity",
    "input_contract_from_params",
]
