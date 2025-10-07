import pytest

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
    assert data["result"]["ai_extract"]["tg2"] == ["TG2 punkt"]
    assert data["result"]["links"]["salgsoppgave_pdf"] == "https://example.org/prospect.pdf"
    assert data["result"]["links"]["confidence"] == pytest.approx(0.95)


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
