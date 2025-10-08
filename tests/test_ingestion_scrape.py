import sys
import types

if "PyPDF2" not in sys.modules:
    fake_module = types.ModuleType("PyPDF2")

    class DummyReader:  # pragma: no cover - simple stub for import
        def __init__(self, *args, **kwargs) -> None:
            self.pages = []

    class DummyWriter:  # pragma: no cover - simple stub for import
        def __init__(self, *args, **kwargs) -> None:
            self._pages = []

        def add_page(self, page) -> None:
            self._pages.append(page)

        def write(self, buffer) -> None:
            buffer.write(b"")

    fake_module.PdfReader = DummyReader
    fake_module.PdfWriter = DummyWriter
    sys.modules["PyPDF2"] = fake_module

from bs4 import BeautifulSoup

from techdom.ingestion.scrape import _build_key_facts, _extract_key_facts_raw, choose_rooms


def test_build_key_facts_captures_key_information() -> None:
    attrs = {
        "Totalpris": "3 117 502 kr",
        "Boligtype": "Leilighet",
        "Eieform": "Eier (Selveier)",
        "Soverom": "2",
        "Rom": "3",
        "Internt bruksareal": "40 m²",
        "Bruksareal": "45 m²",
        "Eksternt bruksareal": "5 m²",
        "Balkong/Terrasse": "6 m²",
        "Etasje": "3.",
        "Byggeår": "1985",
        "Energimerking": "C - Oransje",
        "Tomteareal": "100 m²",
        "Kommunenr": "0301",
    }

    key_facts, derived = _build_key_facts(attrs)
    facts_by_key = {fact["key"]: fact for fact in key_facts}

    assert key_facts[0]["key"] == "total_price"
    assert facts_by_key["total_price"]["value"] == 3117502
    assert facts_by_key["property_type"]["value"] == "Leilighet"
    assert facts_by_key["ownership_type"]["value"].startswith("Eier")
    assert facts_by_key["bedrooms"]["value"] == 2
    assert facts_by_key["rooms"]["value"] == 3
    assert facts_by_key["internal_bra_m2"]["value"] == 40.0
    assert facts_by_key["bra_m2"]["value"] == 45.0
    assert facts_by_key["external_bra_m2"]["value"] == 5.0
    assert facts_by_key["balcony_terrace_m2"]["value"] == 6.0
    assert facts_by_key["floor"]["value"] == 3
    assert facts_by_key["built_year"]["value"] == 1985
    assert facts_by_key["energy_label"]["value"] == "C - Oransje"
    assert facts_by_key["plot_area_m2"]["value"] == 100.0
    assert facts_by_key["kommunenr"]["label"] == "Kommunenr"
    assert facts_by_key["kommunenr"]["value"] == "0301"

    assert derived["total_price"] == 3117502
    assert derived["bedrooms"] == 2
    assert derived["bra_m2"] == 45.0


def test_choose_rooms_prefers_total_rooms_over_bedrooms() -> None:
    attrs = {
        "Soverom": "2",
        "Rom": "4",
    }

    assert choose_rooms(attrs, "") == 4


def test_extract_key_facts_raw_handles_key_info_items() -> None:
    html = """
    <html>
      <body>
        <section data-testid="key-info">
          <div data-testid="key-info-grid">
            <div data-testid="key-info-item">
              <p data-testid="key-info-item-label">Prisantydning</p>
              <p data-testid="key-info-item-value">4 100 000 kr</p>
            </div>
            <div data-testid="key-info-item">
              <span data-testid="key-info-item-label">Felleskostnader</span>
              <span data-testid="key-info-item-value">3 250 kr/mnd</span>
            </div>
            <div data-testid="key-info-item">
              <span data-testid="key-info-item-label">Soverom</span>
              <span data-testid="key-info-item-value">2</span>
            </div>
          </div>
        </section>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    facts = _extract_key_facts_raw(soup)
    assert len(facts) == 3
    assert facts[0]["label"] == "Prisantydning"
    assert facts[0]["value"] == "4 100 000 kr"
    assert facts[1]["label"] == "Felleskostnader"
    assert facts[1]["value"] == "3 250 kr/mnd"
    assert facts[2]["label"] == "Soverom"
    assert facts[2]["value"] == "2"


def test_extract_key_facts_raw_prefers_nokkeltall_section() -> None:
    html = """
    <html>
      <body>
        <section>
          <h2>Nøkkelinfo</h2>
          <div data-testid="key-info-item">
            <span data-testid="key-info-item-label">Boligtype</span>
            <span data-testid="key-info-item-value">Leilighet</span>
          </div>
        </section>
        <section data-testid="key-number-list">
          <h2>Nøkkeltall</h2>
          <dl>
            <dt>Prisantydning</dt>
            <dd>4 100 000 kr</dd>
          </dl>
          <dl>
            <dt>Felleskostnader</dt>
            <dd>3 250 kr/mnd</dd>
          </dl>
        </section>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    facts = _extract_key_facts_raw(soup)
    labels = [fact["label"] for fact in facts]
    assert labels == ["Prisantydning", "Felleskostnader"]
