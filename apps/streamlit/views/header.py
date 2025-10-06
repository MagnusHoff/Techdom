# ui_header.py
import streamlit as st


def _reset_to_landing() -> None:
    """Bring the user back to the landing page and clear temporary data."""

    st.session_state.update(
        {
            "page": "landing",
            "listing_url": "",
            "params": {},
            "computed": None,
            "ai_text": "",
        }
    )
    st.rerun()


def _open_login_modal() -> None:
    """Show the login modal dialog."""

    st.session_state["auth_flow"] = "login"
    st.session_state["show_login_modal"] = True


def _close_login_modal() -> None:
    """Hide the login modal and reset transient auth state."""

    st.session_state["show_login_modal"] = False
    st.session_state["auth_flow"] = "login"


def _render_login_modal() -> None:
    if not st.session_state.get("show_login_modal"):
        return

    with st.modal("Logg inn"):
        st.markdown(
            "<p class=\"td-login-intro\">Logg inn for å lagre og hente analyser." ""
            "<br>Teknisk funksjonalitet kobles på senere.</p>",
            unsafe_allow_html=True,
        )

        submit_clicked = cancel_clicked = False
        with st.form("header_login_form", clear_on_submit=False):
            st.text_input("E-post", key="login_email")
            st.text_input("Passord", type="password", key="login_password")

            actions = st.columns(2, gap="medium")
            with actions[0]:
                submit_clicked = st.form_submit_button(
                    "Logg inn",
                    use_container_width=True,
                )
            with actions[1]:
                cancel_clicked = st.form_submit_button(
                    "Avbryt",
                    use_container_width=True,
                    type="secondary",
                )

        if submit_clicked:
            st.info("Innlogging aktiveres når backend er klar.")
        if cancel_clicked:
            _close_login_modal()

        forgot_clicked = signup_clicked = False
        link_cols = st.columns(2, gap="large")
        with link_cols[0]:
            forgot_clicked = st.button(
                "Glemt passord?",
                key="header_forgot_password",
                use_container_width=True,
                type="secondary",
            )
        with link_cols[1]:
            signup_clicked = st.button(
                "Lag ny bruker",
                key="header_create_account",
                use_container_width=True,
                type="secondary",
            )

        if forgot_clicked:
            st.session_state["auth_flow"] = "forgot"
        if signup_clicked:
            st.session_state["auth_flow"] = "signup"

        flow = st.session_state.get("auth_flow", "login")
        if flow == "forgot":
            st.warning("Glemt passord-funksjonen kommer snart.")
        elif flow == "signup":
            st.info("Registrering av nye brukere settes opp i neste runde.")
        else:
            st.caption("Skriv inn detaljer og logg inn når systemet er klart.")


def render_header() -> None:
    """Render the sticky header with navigation and login entry point."""

    header_container = st.container()
    with header_container:
        st.markdown(
            "<div class=\"td-header-wrapper\">", unsafe_allow_html=True
        )
        cols = st.columns([6, 2, 2], gap="small")
        with cols[0]:
            st.button(
                "Techdom.AI – eiendomsanalyse",
                use_container_width=True,
                on_click=_reset_to_landing,
                type="primary",
            )
        with cols[1]:
            st.button(
                "Ny analyse",
                use_container_width=True,
                on_click=_reset_to_landing,
                type="primary",
            )
        with cols[2]:
            st.button(
                "Logg inn",
                use_container_width=True,
                on_click=_open_login_modal,
                type="primary",
            )
        st.markdown("</div>", unsafe_allow_html=True)

    _render_login_modal()
