"""Scoring helpers for økonomi, tilstand og totalvurdering."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from techdom.domain.analysis.contracts import (
    CalculatedMetrics,
    DecisionVerdict,
    InputContract,
)


@dataclass(frozen=True)
class ScoreSummary:
    """Container for del-scorer og totalvurdering."""

    econ_score: int
    tr_score: int
    total_score: int
    verdict: DecisionVerdict
    tg_cap_used: bool

    def to_dict(self) -> Mapping[str, object]:
        return {
            "econ_score": self.econ_score,
            "tr_score": self.tr_score,
            "total_score": self.total_score,
            "dom": self.verdict.value,
            "tg_cap_used": self.tg_cap_used,
        }


def _clamp(value: float, minimum: int = 0, maximum: int = 100) -> int:
    return int(max(minimum, min(maximum, round(value))))


def _score_cashflow(value_kr_mnd: float) -> float:
    if value_kr_mnd >= 1000:
        return 100.0
    if value_kr_mnd >= 0:
        return 50.0 + (value_kr_mnd / 1000.0) * 50.0
    if value_kr_mnd >= -2000:
        # Map -2000 -> 20, 0 -> 50
        return 20.0 + ((value_kr_mnd + 2000.0) / 2000.0) * 30.0
    return 0.0


def _score_roe(value_pct: float) -> float:
    if value_pct >= 10.0:
        return 100.0
    if value_pct >= 8.0:
        return 70.0 + ((value_pct - 8.0) / 2.0) * 30.0
    if value_pct >= 6.0:
        return 40.0 + ((value_pct - 6.0) / 2.0) * 30.0
    if value_pct >= 0.0:
        return (value_pct / 6.0) * 40.0
    # Negative avkastning slår hardt – fall raskt mot 0
    return max(0.0, 40.0 + value_pct * 8.0)


def _score_break_even(rent_mnd: float, break_even_mnd: float) -> float:
    if rent_mnd <= 0 or not math.isfinite(break_even_mnd):
        return 0.0
    diff_pct = (break_even_mnd - rent_mnd) / rent_mnd
    if diff_pct <= -0.05:
        return 100.0
    if diff_pct >= 0.05:
        return 0.0
    # Innen ±5 % vurderes som OK baseline
    return 50.0


def _score_buffer(
    cashflow_mnd: float, lanekost_mnd: float, felleskost_mnd: float
) -> float:
    base = lanekost_mnd + felleskost_mnd
    if cashflow_mnd <= 0 or base <= 0:
        return 0.0
    ratio = cashflow_mnd / base
    target = 0.2  # 20 % buffer gir full score på denne komponenten
    return min(100.0, max(0.0, (ratio / target) * 100.0))


def _age_adjustment(age_years: Optional[float]) -> int:
    if age_years is None or age_years < 0:
        return 0
    if age_years < 10:
        return 15
    if age_years <= 20:
        return 0
    return -15


def _upgrade_bonus(upgrades: Sequence[str]) -> int:
    if not upgrades:
        return 0
    # Gi 5 poeng per dokumentert tiltak, maks 15
    return int(min(15, len([item for item in upgrades if item.strip()]) * 5))


def _warning_penalty(warnings: Sequence[str]) -> int:
    if not warnings:
        return 0
    return int(min(40, len([item for item in warnings if item.strip()]) * 5))


def compute_scores(
    metrics: CalculatedMetrics,
    input_contract: InputContract,
    tg2_items: Sequence[str],
    tg3_items: Sequence[str],
    *,
    tg_data_available: bool,
    upgrades_recent: Sequence[str] = (),
    warnings: Sequence[str] = (),
    bath_age_years: Optional[float] = None,
    kitchen_age_years: Optional[float] = None,
    roof_age_years: Optional[float] = None,
) -> ScoreSummary:
    """Beregn del-scorer og total score for analysen."""

    cashflow_component = _score_cashflow(metrics.cashflow_mnd)
    roe_component = _score_roe(metrics.roe_pct)
    break_even_component = _score_break_even(
        input_contract.brutto_leie_mnd, metrics.break_even_leie_mnd
    )
    buffer_component = _score_buffer(
        metrics.cashflow_mnd, metrics.lanekost_mnd, input_contract.felleskost_mnd
    )

    econ_score = _clamp(
        (cashflow_component * 0.40)
        + (roe_component * 0.30)
        + (break_even_component * 0.20)
        + (buffer_component * 0.10)
    )

    tg3_penalty = len([item for item in tg3_items if str(item).strip()]) * 15
    tg2_penalty = min(
        len([item for item in tg2_items if str(item).strip()]), 5
    ) * 5
    tg_penalty = tg3_penalty + tg2_penalty

    tr_score_raw = 100 - tg_penalty
    tr_score_raw += _age_adjustment(bath_age_years)
    tr_score_raw += _age_adjustment(kitchen_age_years)
    tr_score_raw += _age_adjustment(roof_age_years)
    tr_score_raw += _upgrade_bonus(upgrades_recent)
    tr_score_raw -= _warning_penalty(warnings)
    tr_score = _clamp(tr_score_raw)

    total_score = math.ceil((econ_score * 0.60) + (tr_score * 0.40))
    tg_cap_used = False
    if not tg_data_available:
        tg_cap_used = True
        total_score = min(total_score, 74)

    total_score = _clamp(total_score)

    if total_score >= 80:
        verdict = DecisionVerdict.BRA
    elif total_score >= 60:
        verdict = DecisionVerdict.OK
    elif total_score >= 40:
        verdict = DecisionVerdict.SVAK
    else:
        verdict = DecisionVerdict.DAARLIG

    return ScoreSummary(
        econ_score=econ_score,
        tr_score=tr_score,
        total_score=total_score,
        verdict=verdict,
        tg_cap_used=tg_cap_used,
    )


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


__all__ = [
    "ScoreSummary",
    "compute_scores",
    "farge_for_break_even_gap",
    "farge_for_cashflow",
    "farge_for_roe",
]
