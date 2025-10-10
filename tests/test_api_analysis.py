from fastapi.testclient import TestClient

from apps.api.main import app
from techdom.domain.analysis_service import (
    AnalysisDecisionContext,
    compute_analysis,
    normalise_params,
)
from techdom.domain.tg_cache import ExtractionResult
from techdom.infrastructure.db import get_session


def test_analysis_endpoint_returns_expected(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)

    payload = {
        "price": "4\u00a0500\u00a0000",
        "equity": "675\u00a0000",
        "interest": "5,1",
        "term_years": "30",
        "rent": "18\u00a0000",
        "hoa": "3\u00a0000",
        "maint_pct": "6,0",
        "vacancy_pct": 0,
        "other_costs": "800",
        "tg2_items": ["TG2 punkt"],
        "tg3_items": ["TG3 funn"],
    }

    response = client.post("/analysis", json=payload)
    assert response.status_code == 200
    data = response.json()

    expected_ctx = AnalysisDecisionContext(
        tg2_items=payload["tg2_items"],
        tg3_items=payload["tg3_items"],
        tg_data_available=True,
    )
    expected = compute_analysis(payload, expected_ctx)

    assert data["metrics"] == expected.metrics
    assert data["normalised_params"] == normalise_params(payload)
    assert data["calculated_metrics"] == expected.calculated_metrics.model_dump()
    assert data["decision_result"] == expected.decision_result.model_dump()
    assert data["decision_ui"] == expected.decision_ui
    assert data["ai_text"] == expected.ai_text


def test_analyze_endpoint_returns_job(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)

    response = client.post("/analyze", json={"finnkode": "123456"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    job_id = payload["job_id"]

    status_resp = client.get(f"/status/{job_id}")
    assert status_resp.status_code == 200
    job_status = status_resp.json()
    assert job_status["status"] == "queued"


def test_get_analysis_tg_details(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _fake_session():
        yield None

    async def _fake_loader(analysis_id: str, session, *, force: bool) -> ExtractionResult:
        return ExtractionResult(
            analysis_id=analysis_id,
            tg2_details=[
                {
                    "label": "Tak",
                    "short": "Tak må fikses",
                    "hover": "TG2 Tak: Tak må fikses.",
                    "tg": 2,
                }
            ],
            tg_version=2,
            updated_at="2024-01-01T00:00:00+00:00",
            pdf_url="https://example.org/prospect.pdf",
        )

    app.dependency_overrides[get_session] = _fake_session
    monkeypatch.setattr("apps.api.main._load_or_extract_tg_details", _fake_loader)

    client = TestClient(app)
    response = client.get("/analysis/654321")
    app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["tg_version"] == 2
    assert isinstance(payload["tg2_details"], list)
    assert payload["tg2_details"][0]["short"] == "Tak må fikses"
