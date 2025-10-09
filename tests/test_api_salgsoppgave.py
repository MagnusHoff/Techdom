from fastapi.testclient import TestClient

from apps.api.main import app
from techdom.services.salgsoppgave import SalgsoppgaveResult


def test_salgsoppgave_endpoint_found(monkeypatch):
    client = TestClient(app)

    async def fake_retrieve(finnkode_or_url: str, *, extra_terms=None, session=None):
        assert finnkode_or_url == "123456"
        return SalgsoppgaveResult(
            status="found",
            original_pdf_url="https://www.finn.no/prospect.pdf",
            stable_pdf_url="https://cdn.oursite/123456/abc.pdf",
            filesize_bytes=123_456,
            sha256="abc123",
            confidence=0.95,
            log=["cache_hit"],
        )

    monkeypatch.setattr("apps.api.main.retrieve_salgsoppgave", fake_retrieve)

    response = client.get("/salgsoppgave?finn=123456")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "found"
    assert data["stable_pdf_url"] == "https://cdn.oursite/123456/abc.pdf"
    assert data["confidence"] == 0.95
    assert data["log"] == ["cache_hit"]


def test_salgsoppgave_endpoint_bad_request(monkeypatch):
    client = TestClient(app)

    async def fake_retrieve(_value: str, *, extra_terms=None, session=None):
        raise ValueError("ugyldig")

    monkeypatch.setattr("apps.api.main.retrieve_salgsoppgave", fake_retrieve)

    response = client.get("/salgsoppgave?finn=not-a-code")
    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"] == "ugyldig"
