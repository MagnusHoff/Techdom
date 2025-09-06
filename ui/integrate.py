# ui/integrate.py
import os
import streamlit as st
from core.rent import get_rent_suggestion

FEATURE_RENT_COMPS = os.getenv("FEATURE_RENT_COMPS", "false").lower() == "true"


def render_rent_input(
    *,
    label: str = "Brutto leie (kr/mnd)",
    key: str = "brutto_leie",
    address: str | None = None,
    areal_m2: float | None = None,
    rom: int | None = None,
    type: str | None = None,  # "leilighet", "hybel", ...
) -> int:
    """
    Viser tallfelt + liten 'Søk comps'-knapp på samme linje.
    Hvis knappen trykkes, henter vi forslag og fyller feltet automatisk.
    Returnerer gjeldende verdi (int).
    """
    # startverdi (fra state hvis finnes)
    start_value = int(st.session_state.get(key, 0) or 0)

    col_input, col_btn = st.columns([5, 1])
    with col_input:
        value = st.number_input(
            label, min_value=0, step=100, key=key, value=start_value
        )

    with col_btn:
        # Vis knapp kun hvis flagget er på
        if FEATURE_RENT_COMPS:
            if st.button("Søk comps", key=f"{key}_search_btn"):
                if not all([address, areal_m2, type]):
                    st.warning("Trenger address, areal_m2 og type for comps.")
                else:
                    s = get_rent_suggestion(
                        address=address,
                        areal_m2=areal_m2,
                        rom=rom,
                        type=type,
                    )
                    # sett verdier i state + vis kort feedback
                    st.session_state[key] = int(s.suggested_rent)
                    st.toast(
                        f"Forslag: {s.suggested_rent} kr (CI {s.low_ci}–{s.high_ci})"
                    )
        else:
            # litet, diskret plassholder hvis feature er av
            st.caption("")

    return int(st.session_state.get(key, value))
