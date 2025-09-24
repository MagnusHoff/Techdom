# ui/integrate.py
from __future__ import annotations

import os
from typing import Dict, cast

import streamlit as st
from core.rent import get_rent_by_csv, RentEstimate

FEATURE_RENT_COMPS = os.getenv("FEATURE_RENT_COMPS", "false").lower() == "true"


def render_rent_input(
    *,
    label: str = "Brutto leie (kr/mnd)",
    key: str = "brutto_leie",
    address: str | None = None,
    areal_m2: float | None = None,
    rom: int | None = None,
    type: str | None = None,  # beholdt for bakoverkomp., brukes ikke nå
) -> int:
    """
    Viser tallfelt + liten 'Søk comps'-knapp på samme linje.
    Hvis knappen trykkes, henter vi forslag og fyller feltet automatisk.
    Returnerer gjeldende verdi (int).
    """
    start_value = int(st.session_state.get(key, 0) or 0)

    col_input, col_btn = st.columns([5, 1])
    with col_input:
        value = st.number_input(
            label, min_value=0, step=100, key=key, value=start_value
        )

    with col_btn:
        if FEATURE_RENT_COMPS:
            if st.button("Søk comps", key=f"{key}_search_btn"):
                if not address or areal_m2 is None:
                    st.warning("Trenger address og areal_m2 for comps.")
                else:
                    # Bygg info som Dict[str, object] (ikke plain dict[str, str])
                    info: Dict[str, object] = {"address": address}
                    est: RentEstimate | None = get_rent_by_csv(
                        info=info,
                        area_m2=float(areal_m2),
                        rooms=rom,
                        city_hint=None,
                    )
                    if est is None:
                        st.warning("Fant ikke forslag for denne adressen.")
                    else:
                        st.session_state[key] = int(est.gross_rent)
                        conf_pct = int(round(est.confidence * 100))
                        st.toast(
                            f"Forslag: {est.gross_rent:,} kr · {est.city} / {est.bucket} · "
                            f"{est.kr_per_m2:.0f} kr/m² · konfidens {conf_pct}%"
                        )
        else:
            st.caption("")

    return int(st.session_state.get(key, value))
