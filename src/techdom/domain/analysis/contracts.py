"""Pydantic contracts and helpers for property analyses."""
from __future__ import annotations

from enum import Enum
from typing import Iterable, List, Optional

from pydantic import BaseModel, Field

from techdom.processing.compute import compute_metrics


class InputContract(BaseModel):
    """Inputverdier brukt i analyse av lønnsomhet."""

    kjopesum: float
    egenkapital: float
    rente_pct_pa: float
    lanetid_ar: int
    brutto_leie_mnd: float
    felleskost_mnd: float
    vedlikehold_pct_av_leie: float
    andre_kost_mnd: float


class CalculatedMetrics(BaseModel):
    """Utledede nøkkeletall fra `compute_metrics`."""

    cashflow_mnd: float
    break_even_leie_mnd: float
    noi_aar: float
    roe_pct: float
    lanekost_mnd: float
    aarlig_nedbetaling_lan: float


class DecisionFacts(BaseModel):
    risk_flags: List[str] = Field(default_factory=list)
    positives: List[str] = Field(default_factory=list)


class DecisionVerdict(str, Enum):
    DAARLIG = "Dårlig"
    SVAK = "Svak"
    OK = "OK"
    BRA = "Bra"


class KeyFigure(BaseModel):
    navn: str
    verdi: str
    farge: str


class DecisionResult(BaseModel):
    score_0_100: int
    dom: DecisionVerdict
    econ_score_0_100: int
    tr_score_0_100: int
    tg_cap_used: bool = False
    status_setning: str
    tiltak: List[str] = Field(default_factory=list)
    positivt: List[str] = Field(default_factory=list)
    risiko: List[str] = Field(default_factory=list)
    nokkel_tall: List[KeyFigure] = Field(default_factory=list)
    impl_version: str = "analysis_v1.0"
    dom_notat: Optional[str] = None


if hasattr(DecisionResult, "model_rebuild"):
    DecisionResult.model_rebuild()


def build_calculated_metrics(input_contract: InputContract) -> CalculatedMetrics:
    """Mapper resultatet fra `compute_metrics` inn i Pydantic-modellen."""

    metrics = compute_metrics(
        price=input_contract.kjopesum,
        equity=input_contract.egenkapital,
        interest=input_contract.rente_pct_pa,
        term_years=input_contract.lanetid_ar,
        rent=input_contract.brutto_leie_mnd,
        hoa=input_contract.felleskost_mnd,
        maint_pct=input_contract.vedlikehold_pct_av_leie,
        vacancy_pct=0.0,
        other_costs=input_contract.andre_kost_mnd,
    )

    return CalculatedMetrics(
        cashflow_mnd=metrics["cashflow"],
        break_even_leie_mnd=metrics["break_even"],
        noi_aar=metrics["noi_year"],
        roe_pct=metrics["total_equity_return_pct"],
        lanekost_mnd=metrics["m_payment"],
        aarlig_nedbetaling_lan=metrics["principal_reduction_year"],
    )


__all__ = [
    "InputContract",
    "CalculatedMetrics",
    "DecisionFacts",
    "DecisionVerdict",
    "KeyFigure",
    "DecisionResult",
    "build_calculated_metrics",
]
