import sys
import types

import pytest


class _StubOpenAI:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("OpenAI client initialisering skal ikke skje i tester")


_playwright_sync_api = types.SimpleNamespace(
    sync_playwright=lambda: (_ for _ in ()).throw(RuntimeError("Playwright skal ikke brukes i tester")),
    TimeoutError=RuntimeError,
)

sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=_StubOpenAI))
sys.modules.setdefault("playwright", types.SimpleNamespace(sync_api=_playwright_sync_api))
sys.modules.setdefault("playwright.sync_api", _playwright_sync_api)

from techdom.processing.rates import RateMeta
from techdom.processing.rent.logic import RentEstimate
from techdom.services.prospect_jobs import ProspectJobService
from techdom.services.prospect_pipeline import ProspectAnalysisPipeline


@pytest.fixture(autouse=True)
def _clear_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_pipeline_updates_job(monkeypatch):
    service = ProspectJobService(redis_url=None)
    job = service.create(
        "654321",
        payload={"finn_url": "https://example.org/fake"},
        enqueue=False,
    )

    pipeline = ProspectAnalysisPipeline(service)

    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.scrape_finn",
        lambda url: {"total_price": 2_500_000, "hoa_month": 3_000, "area_m2": 70, "rooms": 3},
    )

    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_rent_by_csv",
        lambda info, area, rooms: RentEstimate(
            gross_rent=18_000,
            kr_per_m2=350.0,
            bucket="Sentrum",
            city="Oslo",
            confidence=0.9,
            note="test",
            updated="2024-01",
        ),
    )

    rate_meta = RateMeta(
        source="test",
        dnb_rate=5.1,
        policy_rate=4.5,
        margin_used=0.6,
        calibrated_at="2024-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_interest_estimate",
        lambda return_meta=True: (5.0, rate_meta),
    )

    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.fetch_prospectus_from_finn",
        lambda url: (b"%PDFtest", "https://example.org/prospect.pdf", {"step": "ok"}),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.save_pdf_locally",
        lambda finnkode, data: f"/tmp/{finnkode}.pdf",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline._verify_pdf_head",
        lambda url, referer=None: (True, url, 0.9, False),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.extract_pdf_text_from_bytes",
        lambda data: "Dette er en test av PDF-innhold.",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.extract_tg_from_pdf_bytes",
        lambda data: {
            "markdown": "TG2\nTak må fikses.\n\nTG3\nBad må fikses.",
            "json": {
                "TG2": [
                    {"komponent": "Tak", "grunn": "Tak må fikses.", "kilde_side": "12"}
                ],
                "TG3": [
                    {"komponent": "Bad", "grunn": "Bad må fikses.", "kilde_side": "14"}
                ],
                "missing": [],
            },
        },
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.analyze_prospectus",
        lambda text: {
            "summary_md": "Kort oppsummering",
            "tg2": ["TG2 punkt"],
            "tg3": ["TG3 funn"],
        },
    )

    pipeline.run(job)

    data = service.get(job.id)
    assert data is not None
    assert data["status"] == "done"
    assert data["pdf_path"].endswith("654321.pdf")
    assert data["pdf_url"] == "https://example.org/prospect.pdf"
    assert data["result"]["analysis"]["metrics"]
    assert data["artifacts"]["analysis_params"]["price"] == 2_500_000
    tg2_points = data["result"]["ai_extract"]["tg2"]
    tg3_points = data["result"]["ai_extract"]["tg3"]
    assert "TG2 punkt" in tg2_points
    assert "TG2 Tak: Tak må fikses. (Side 12)" in tg2_points
    assert "TG3 funn" in tg3_points
    assert "TG3 Bad: Bad må fikses. (Side 14)" in tg3_points
    tg2_details = data["result"]["ai_extract"].get("tg2_details")
    tg3_details = data["result"]["ai_extract"].get("tg3_details")
    assert tg2_details and tg2_details[0]["label"].startswith("Tak")
    assert tg2_details[0]["detail"].startswith("TG2 Tak")
    assert tg3_details and tg3_details[0]["label"].startswith("Bad")
    assert tg3_details[0]["detail"].startswith("TG3 Bad")
    assert data["result"]["links"]["salgsoppgave_pdf"] == "https://example.org/prospect.pdf"
    assert data["result"]["links"]["confidence"] == pytest.approx(0.95)
    assert data["artifacts"]["tg_extract"]["json"]["TG2"][0]["komponent"] == "Tak"


def test_pipeline_handles_protected_pdf(monkeypatch):
    service = ProspectJobService(redis_url=None)
    job = service.create(
        "123456",
        payload={"finn_url": "https://example.org/protected"},
        enqueue=False,
    )

    pipeline = ProspectAnalysisPipeline(service)

    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.scrape_finn",
        lambda url: {"total_price": 2_000_000, "hoa_month": 2_500, "area_m2": 60, "rooms": 2},
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_rent_by_csv",
        lambda info, area, rooms: None,
    )
    rate_meta = RateMeta(
        source="test",
        dnb_rate=5.1,
        policy_rate=4.5,
        margin_used=0.6,
        calibrated_at="2024-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_interest_estimate",
        lambda return_meta=True: (5.0, rate_meta),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.fetch_prospectus_from_finn",
        lambda url: (b"%PDFtest", "https://example.org/prospect.pdf", {"step": "ok"}),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.save_pdf_locally",
        lambda finnkode, data: f"/tmp/{finnkode}.pdf",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline._verify_pdf_head",
        lambda url, referer=None: (False, None, 0.0, True),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.extract_pdf_text_from_bytes",
        lambda data: "PDF-innhold brukt til test.",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.extract_tg_from_pdf_bytes",
        lambda data: {
            "markdown": "TG2\n\nTG3",
            "json": {"TG2": [], "TG3": [], "missing": []},
        },
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.analyze_prospectus",
        lambda text: {"tg2": [], "tg3": []},
    )

    pipeline.run(job)

    data = service.get(job.id)
    assert data is not None
    assert data["status"] == "done"
    assert data["pdf_url"] is None
    assert data["result"]["links"]["salgsoppgave_pdf"] is None
    assert data["result"]["links"]["confidence"] == pytest.approx(0.0)
    assert data["result"]["links"]["message"] == "Beskyttet – last ned lokalt."
    assert not data["result"]["ai_extract"].get("tg2_details")
    assert not data["result"]["ai_extract"].get("tg3_details")


def test_pipeline_continues_without_pdf(monkeypatch):
    service = ProspectJobService(redis_url=None)
    job = service.create(
        "000111",
        payload={"finn_url": "https://example.org/missing"},
        enqueue=False,
    )

    pipeline = ProspectAnalysisPipeline(service)

    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.scrape_finn",
        lambda url: {"total_price": 3_000_000, "hoa_month": 2_000, "area_m2": 75, "rooms": 3},
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_rent_by_csv",
        lambda info, area, rooms: None,
    )
    rate_meta = RateMeta(
        source="test",
        dnb_rate=5.1,
        policy_rate=4.5,
        margin_used=0.6,
        calibrated_at="2024-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_interest_estimate",
        lambda return_meta=True: (5.0, rate_meta),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.fetch_prospectus_from_finn",
        lambda url: (None, None, {"step": "no_pdf"}),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline._verify_pdf_head",
        lambda url, referer=None: (False, None, 0.0, False),
    )

    pipeline.run(job)

    data = service.get(job.id)
    assert data is not None
    assert data["status"] == "done"
    assert data["pdf_path"] is None
    assert data["result"]["analysis"]["input_params"]["price"] == 3_000_000
    assert data["result"]["links"]["salgsoppgave_pdf"] is None
    assert data["result"]["links"]["confidence"] == pytest.approx(0.0)


def test_pipeline_handles_fetch_exception(monkeypatch):
    service = ProspectJobService(redis_url=None)
    job = service.create(
        "987654",
        payload={"finn_url": "https://example.org/error"},
        enqueue=False,
    )

    pipeline = ProspectAnalysisPipeline(service)

    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.scrape_finn",
        lambda url: {"total_price": 4_200_000, "hoa_month": 2_200, "area_m2": 68, "rooms": 3},
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_rent_by_csv",
        lambda info, area, rooms: None,
    )
    rate_meta = RateMeta(
        source="test",
        dnb_rate=4.9,
        policy_rate=4.3,
        margin_used=0.6,
        calibrated_at="2024-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_interest_estimate",
        lambda return_meta=True: (5.1, rate_meta),
    )

    def _raise_fetch_error(_url: str):
        raise RuntimeError("Fant ikke prospekt PDF")

    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.fetch_prospectus_from_finn",
        _raise_fetch_error,
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline._verify_pdf_head",
        lambda url, referer=None: (False, None, 0.0, False),
    )

    pipeline.run(job)

    data = service.get(job.id)
    assert data is not None
    assert data["status"] == "done"
    assert data["pdf_path"] is None
    assert data["pdf_url"] is None
    assert data["message"] == "Analyse fullført (uten salgsoppgave)"
    assert data["result"]["analysis"]["input_params"]["price"] == 4_200_000
    assert data["result"]["links"]["salgsoppgave_pdf"] is None
    assert data["result"]["links"]["confidence"] == pytest.approx(0.0)
    assert "fetch" in data["debug"]
    assert "error" in data["debug"]["fetch"]
    assert data["message"] == "Analyse fullført (uten salgsoppgave)"


def test_pipeline_uses_finn_key_numbers_when_missing_listing_values(monkeypatch):
    service = ProspectJobService(redis_url=None)
    job = service.create(
        "555666",
        payload={"finn_url": "https://example.org/missing-values"},
        enqueue=False,
    )

    pipeline = ProspectAnalysisPipeline(service)

    # Listing lacks price/felleskost values so the pipeline must fall back to FINN key numbers.
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.scrape_finn",
        lambda url: {"address": "Testveien 1"},
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.scrape_finn_key_numbers",
        lambda url, include_raw=True: (
            {"totalpris": 5_500_000, "felleskostnader": 3_200},
            [{"label": "Totalpris", "value": "5 500 000", "order": 10}],
        ),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_rent_by_csv",
        lambda info, area, rooms: None,
    )
    rate_meta = RateMeta(
        source="test",
        dnb_rate=4.8,
        policy_rate=4.25,
        margin_used=0.55,
        calibrated_at="2024-01-01T00:00:00Z",
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.get_interest_estimate",
        lambda return_meta=True: (4.9, rate_meta),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline.fetch_prospectus_from_finn",
        lambda url: (None, None, {"step": "no_pdf"}),
    )
    monkeypatch.setattr(
        "techdom.services.prospect_pipeline._verify_pdf_head",
        lambda url, referer=None: (False, None, 0.0, False),
    )

    pipeline.run(job)

    data = service.get(job.id)
    assert data is not None
    assert data["status"] == "done"
    input_params = data["result"]["analysis"]["input_params"]
    assert input_params["price"] == 5_500_000
    assert input_params["hoa"] == 3_200
    listing = data["result"]["listing"]
    assert listing["total_price"] == 5_500_000
    assert listing["hoa_month"] == 3_200
    finn_key_numbers = data["result"]["finn_key_numbers"]
    assert finn_key_numbers["values"]["totalpris"] == 5_500_000
    assert finn_key_numbers["values"]["felleskostnader"] == 3_200
