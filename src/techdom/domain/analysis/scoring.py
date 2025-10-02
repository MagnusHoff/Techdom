"""Helpers for scoring økonomi og dom for boligprosjekt."""
from __future__ import annotations

from typing import Tuple

from techdom.domain.analysis.contracts import (
    CalculatedMetrics,
    DecisionVerdict,
    InputContract,
)
from techdom.domain.analysis.risk import calc_total_score
from techdom.infrastructure.configs import risk as risk_config


def farge_for_cashflow(value_kr_mnd: float) -> str:
    """Returner farge basert på månedlig cashflow."""

    if value_kr_mnd < -2000:
        return "red"
    if value_kr_mnd <= 0:
        return "orange"
    return "green"


def farge_for_roe(value_pct: float) -> str:
    """Returner farge basert på avkastning på egenkapital."""

    if value_pct < 5:
        return "red"
    if value_pct < 10:
        return "orange"
    return "green"


def farge_for_break_even_gap(faktisk_leie: float, break_even_leie: float) -> str:
    """Returner farge basert på gapet mellom faktisk leie og break-even-nivå."""

    gap = break_even_leie - faktisk_leie
    terskel = faktisk_leie * 0.05

    if gap > terskel:
        return "red"
    if gap >= -terskel:
        return "orange"
    return "green"


def _calculate_econ_score(
    metrics: CalculatedMetrics, input_contract: InputContract
) -> int:
    cashflow_base = _cashflow_base_score(metrics.cashflow_mnd)
    roe_base = _roe_base_score(metrics.roe_pct)
    buffer_base = _break_even_base_score(
        faktisk_leie=input_contract.brutto_leie_mnd,
        break_even_leie=metrics.break_even_leie_mnd,
    )

    econ_score = (
        _vektet_score(cashflow_base, 40)
        + _vektet_score(roe_base, 40)
        + _vektet_score(buffer_base, 20)
    )
    econ_score_int = int(round(econ_score))
    return max(0, min(100, econ_score_int))


def beregn_score_og_dom(
    metrics: CalculatedMetrics,
    input_contract: InputContract,
    risk_score: int = 100,
    has_tg_data: bool = False,
) -> Tuple[int, DecisionVerdict, int, bool]:
    """Beregn total score (0–100) og dom basert på deterministiske regler."""

    econ_score_int = _calculate_econ_score(metrics, input_contract)

    uncapped_total = calc_total_score(econ_score_int, risk_score, True)
    total_score = calc_total_score(econ_score_int, risk_score, has_tg_data)

    used_no_tg_cap = False
    if not has_tg_data and uncapped_total >= 75:
        total_score = int(risk_config.MAX_TOTAL_IF_NO_TG)
        dom = DecisionVerdict.OK
        used_no_tg_cap = True
    elif total_score >= 75:
        dom = DecisionVerdict.BRA
    elif total_score >= 50:
        dom = DecisionVerdict.OK
    else:
        dom = DecisionVerdict.DAARLIG

    return total_score, dom, econ_score_int, used_no_tg_cap


def _vektet_score(base_score: int, weight: int) -> float:
    return (base_score / 100.0) * weight


def _cashflow_base_score(value_kr_mnd: float) -> int:
    if value_kr_mnd > 1000:
        return 100
    if value_kr_mnd >= 0:
        return 50
    if value_kr_mnd >= -2000:
        return 20
    return 0


def _roe_base_score(value_pct: float) -> int:
    if value_pct >= 10:
        return 100
    if value_pct >= 8:
        return 70
    if value_pct >= 6:
        return 40
    return 0


def _break_even_base_score(faktisk_leie: float, break_even_leie: float) -> int:
    gap = break_even_leie - faktisk_leie
    terskel = abs(faktisk_leie) * 0.05

    if gap < -terskel:
        return 100
    if gap <= terskel:
        return 50
    return 0


__all__ = [
    "beregn_score_og_dom",
    "farge_for_break_even_gap",
    "farge_for_cashflow",
    "farge_for_roe",
]
