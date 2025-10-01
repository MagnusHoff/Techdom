"""Contracts and data structures for property analysis results."""
from __future__ import annotations

from enum import Enum
import math
from typing import List, Tuple, Dict, Any, Iterable, Optional

from pydantic import BaseModel, Field

from core.compute import compute_metrics
from core.configs import risk as risk_config


class InputContract(BaseModel):
    kjopesum: float
    egenkapital: float
    rente_pct_pa: float
    lanetid_ar: int
    brutto_leie_mnd: float
    felleskost_mnd: float
    vedlikehold_pct_av_leie: float
    andre_kost_mnd: float


class CalculatedMetrics(BaseModel):
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
    OK = "OK"
    BRA = "Bra"


class KeyFigure(BaseModel):
    navn: str
    verdi: str
    farge: str


class DecisionResult(BaseModel):
    score_0_100: int
    dom: DecisionVerdict
    status_setning: str
    tiltak: List[str] = Field(default_factory=list)
    positivt: List[str] = Field(default_factory=list)
    risiko: List[str] = Field(default_factory=list)
    nokkel_tall: List[KeyFigure] = Field(default_factory=list)
    impl_version: str = "analysis_v1.0"
    dom_notat: Optional[str] = None


DecisionResult.model_rebuild()


def build_calculated_metrics(input: InputContract) -> CalculatedMetrics:
    """Map existing compute metrics into the CalculatedMetrics contract."""

    metrics = compute_metrics(
        price=input.kjopesum,
        equity=input.egenkapital,
        interest=input.rente_pct_pa,
        term_years=input.lanetid_ar,
        rent=input.brutto_leie_mnd,
        hoa=input.felleskost_mnd,
        maint_pct=input.vedlikehold_pct_av_leie,
        vacancy_pct=0.0,
        other_costs=input.andre_kost_mnd,
    )

    return CalculatedMetrics(
        cashflow_mnd=metrics["cashflow"],
        break_even_leie_mnd=metrics["break_even"],
        noi_aar=metrics["noi_year"],
        roe_pct=metrics["total_equity_return_pct"],
        lanekost_mnd=metrics["m_payment"],
        aarlig_nedbetaling_lan=metrics["principal_reduction_year"],
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


def build_decision_result(
    input_contract: InputContract,
    calc: CalculatedMetrics,
    tg2_items: Optional[Iterable[str]] = None,
    tg3_items: Optional[Iterable[str]] = None,
    tg_data_available: Optional[bool] = None,
) -> DecisionResult:
    """Lag deterministisk beslutningsresultat for visning i UI."""

    tg2_list = list(tg2_items or [])
    tg3_list = list(tg3_items or [])
    has_tg_data = (
        tg_data_available
        if tg_data_available is not None
        else bool(tg2_list or tg3_list)
    )
    risk_score = calc_risk_score(
        tg2_list,
        tg3_list,
        has_tg_data=has_tg_data,
    )

    score, dom, _econ_score, used_no_tg_cap = beregn_score_og_dom(
        calc,
        input_contract,
        risk_score=risk_score,
        has_tg_data=has_tg_data,
    )

    dom_notat: Optional[str] = None
    if used_no_tg_cap:
        dom_notat = "Dom basert på økonomi. Teknisk risiko ikke vurdert ennå."

    risiko_entries: List[str] = []
    for item in tg3_list:
        risiko_entries.append(f"TG3: {item}")
    for item in tg2_list:
        risiko_entries.append(f"TG2: {item}")
    risiko_entries = risiko_entries[:8]

    if calc.cashflow_mnd < 0:
        status = "Marginal lønnsomhet. Cashflow negativ, men kan bedres med tiltak."
    elif calc.roe_pct < 8:
        status = "Greit avkastningsnivå. Vurder tiltak for å styrke lønnsomhet."
    else:
        status = "Solid lønnsomhet med håndterbare forutsetninger."

    tiltak: List[str] = []
    if calc.cashflow_mnd < 0:
        tiltak.extend([
            "Forhandle pris ned",
            "Vurder moderat leieøkning",
        ])
    if calc.break_even_leie_mnd > input_contract.brutto_leie_mnd:
        tiltak.append("Senk driftskostnader/felleskost om mulig")
    if calc.roe_pct < 8:
        tiltak.append("Øk leie eller redusér EK-binding/forhandle rente")
    tiltak = tiltak[:4]

    positivt: List[str] = []
    if calc.cashflow_mnd > 0:
        positivt.append("Positiv månedlig cashflow")
    if calc.roe_pct >= 10:
        positivt.append("Sterk avkastning på egenkapital")
    if calc.break_even_leie_mnd <= input_contract.brutto_leie_mnd * 0.95:
        positivt.append("God buffer mot nullpunkt")
    positivt = positivt[:4]

    nokkel_tall = [
        KeyFigure(
            navn="Månedlig overskudd",
            verdi=_format_currency(calc.cashflow_mnd, suffix=" kr/mnd"),
            farge=farge_for_cashflow(calc.cashflow_mnd),
        ),
        KeyFigure(
            navn="Leie for å gå i null",
            verdi=_format_currency(calc.break_even_leie_mnd, suffix=" kr/mnd"),
            farge="neutral",
        ),
        KeyFigure(
            navn="Årlig nettoinntekt",
            verdi=_format_currency(calc.noi_aar, suffix=" kr"),
            farge="neutral",
        ),
        KeyFigure(
            navn="Årlig nedbetaling på lån",
            verdi=_format_currency(calc.aarlig_nedbetaling_lan, suffix=" kr"),
            farge="neutral",
        ),
        KeyFigure(
            navn="Månedlig lånekostnader",
            verdi=_format_currency(calc.lanekost_mnd, suffix=" kr/mnd"),
            farge="neutral",
        ),
        KeyFigure(
            navn="Avkastning på egenkapital",
            verdi=f"{calc.roe_pct:.1f} %",
            farge=farge_for_roe(calc.roe_pct),
        ),
    ]

    return DecisionResult(
        score_0_100=score,
        dom=dom,
        status_setning=status,
        tiltak=tiltak,
        positivt=positivt,
        risiko=risiko_entries,
        nokkel_tall=nokkel_tall,
        dom_notat=dom_notat,
    )


def _format_currency(value: float, suffix: str) -> str:
    return f"{value:,.0f}{suffix}".replace(",", " ")


def map_decision_to_ui(decision: DecisionResult) -> Dict[str, Any]:
    """Lag datasett for UI basert på en DecisionResult-instans."""

    return {
        "status": {
            "score": decision.score_0_100,
            "dom": decision.dom.value,
            "setning": decision.status_setning,
        },
        "tiltak": list(decision.tiltak),
        "positivt": list(decision.positivt),
        "risiko": list(decision.risiko),
        "nokkel_tall": [
            {
                "navn": k.navn,
                "verdi": k.verdi,
                "farge": k.farge,
            }
            for k in decision.nokkel_tall
        ],
        "scorelinjal": {
            "value": decision.score_0_100,
            "farge": _dom_til_farge(decision.dom),
        },
        "dom_notat": decision.dom_notat,
    }


def _dom_til_farge(dom: DecisionVerdict) -> str:
    mapping = {
        DecisionVerdict.DAARLIG: "red",
        DecisionVerdict.OK: "orange",
        DecisionVerdict.BRA: "green",
    }
    return mapping.get(dom, "neutral")


def calc_risk_score(
    tg2_items: Iterable[str],
    tg3_items: Iterable[str],
    has_tg_data: bool = False,
) -> int:
    """Calculate risk score based on TG2/TG3 findings."""

    tg2_list = list(tg2_items)
    tg3_list = list(tg3_items)

    if not has_tg_data and not tg2_list and not tg3_list:
        return int(risk_config.DEFAULT_RISK_SCORE_NO_DATA)

    tg3_count = len(tg3_list)
    tg2_count = min(len(tg2_list), int(risk_config.CAP_TG2_ITEMS))

    penalty = (tg3_count * int(risk_config.PENALTY_TG3_PER_ITEM)) + (
        tg2_count * int(risk_config.PENALTY_TG2_PER_ITEM)
    )
    penalty = min(penalty, int(risk_config.MAX_RISK_PENALTY))

    risk_score = max(0, 100 - penalty)
    return int(risk_score)


def calc_total_score(
    econ_score_0_100: float, risk_score_0_100: float, has_tg_data: bool
) -> int:
    """Combine economic score and risk score into a total score."""

    econ = float(econ_score_0_100)
    risk = float(risk_score_0_100)

    weighted = ((1.0 - risk_config.WEIGHT_TOTAL_RISK) * econ) + (
        risk_config.WEIGHT_TOTAL_RISK * risk
    )
    total = math.ceil(weighted)

    if not has_tg_data:
        total = min(total, int(risk_config.MAX_TOTAL_IF_NO_TG))

    return int(max(0, min(100, total)))
