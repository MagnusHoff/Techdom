"""Oppbygning av beslutningsresultat for visning i UI."""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

from techdom.domain.analysis.contracts import (
    CalculatedMetrics,
    DecisionResult,
    DecisionVerdict,
    InputContract,
    KeyFigure,
)
from techdom.domain.analysis.scoring import (
    ScoreSummary,
    compute_scores,
    farge_for_break_even_gap,
    farge_for_cashflow,
    farge_for_roe,
)


def build_decision_result(
    input_contract: InputContract,
    calc: CalculatedMetrics,
    tg2_items: Optional[Iterable[str]] = None,
    tg3_items: Optional[Iterable[str]] = None,
    tg_data_available: Optional[bool] = None,
    upgrades: Optional[Iterable[str]] = None,
    warnings: Optional[Iterable[str]] = None,
    bath_age_years: Optional[float] = None,
    kitchen_age_years: Optional[float] = None,
    roof_age_years: Optional[float] = None,
) -> DecisionResult:
    """Lag deterministisk beslutningsresultat for visning i UI."""

    tg2_list = list(tg2_items or [])
    tg3_list = list(tg3_items or [])
    has_tg_data = (
        tg_data_available
        if tg_data_available is not None
        else bool(tg2_list or tg3_list)
    )

    upgrades_list: Sequence[str] = list(upgrades or [])
    warning_list: Sequence[str] = list(warnings or [])

    summary: ScoreSummary = compute_scores(
        calc,
        input_contract,
        tg2_list,
        tg3_list,
        tg_data_available=has_tg_data,
        upgrades_recent=upgrades_list,
        warnings=warning_list,
        bath_age_years=bath_age_years,
        kitchen_age_years=kitchen_age_years,
        roof_age_years=roof_age_years,
    )

    dom_notat: Optional[str] = None

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
        score_0_100=summary.total_score,
        dom=summary.verdict,
        econ_score_0_100=summary.econ_score,
        tr_score_0_100=summary.tr_score,
        tg_cap_used=summary.tg_cap_used,
        status_setning=status,
        tiltak=tiltak,
        positivt=positivt,
        risiko=risiko_entries,
        nokkel_tall=nokkel_tall,
        dom_notat=dom_notat,
    )


def _format_currency(value: float, suffix: str) -> str:
    return f"{value:,.0f}{suffix}".replace(",", " ")


def map_decision_to_ui(decision: DecisionResult) -> dict[str, Any]:
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
        "score_breakdown": [
            {
                "id": "econ",
                "label": "Økonomi",
                "value": decision.econ_score_0_100,
            },
            {
                "id": "tr",
                "label": "Tilstand",
                "value": decision.tr_score_0_100,
            },
        ],
        "tg_cap_used": decision.tg_cap_used,
        "dom_notat": decision.dom_notat,
    }


def _dom_til_farge(dom: DecisionVerdict) -> str:
    mapping = {
        DecisionVerdict.DAARLIG: "red",
        DecisionVerdict.SVAK: "orange",
        DecisionVerdict.OK: "yellow",
        DecisionVerdict.BRA: "green",
    }
    return mapping.get(dom, "neutral")


__all__ = ["build_decision_result", "map_decision_to_ui"]
