import streamlit as st


def render_footer() -> None:
    """Render den tilpassede footer-seksjonen nederst på siden."""
    instagram_svg = (
        "<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" "
        "stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\" "
        "aria-hidden=\"true\"><rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"4.5\"/>"
        "<circle cx=\"12\" cy=\"12\" r=\"3.8\"/><circle cx=\"17\" cy=\"7\" r=\"1\"/></svg>"
    )
    mail_svg = (
        "<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" "
        "stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\" "
        "aria-hidden=\"true\"><rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2.2\"/>"
        "<polyline points=\"4 7 12 12.5 20 7\"/></svg>"
    )

    footer_html = f"""
    <div class=\"td-footer-wrapper\">
      <div class=\"td-footer-links\">
        <a class=\"td-footer-link\" href=\"https://instagram.com/techdom.ai\" target=\"_blank\" rel=\"noopener noreferrer\">
          <span class=\"td-footer-icon\">{instagram_svg}</span>
          <span>Instagram: techdom.ai</span>
        </a>
        <a class=\"td-footer-link\" href=\"mailto:techdomai@techdomai.com\">
          <span class=\"td-footer-icon\">{mail_svg}</span>
          <span>Mail: techdomai@techdomai.com</span>
        </a>
      </div>
      <div class=\"td-footer-disclaimer\">
        Analysene fra Techdom.ai er kun ment som veiledende informasjon. Vi kan ikke garantere fullstendig nøyaktighet, og innholdet erstatter ikke profesjonell rådgivning. Bruk alltid egne vurderinger eller søk faglig hjelp før du tar investeringsbeslutninger.
      </div>
    </div>
    """

    st.markdown(footer_html, unsafe_allow_html=True)
