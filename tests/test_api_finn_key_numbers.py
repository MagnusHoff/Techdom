import requests
from fastapi.testclient import TestClient

from apps.api.main import app


def test_finn_key_numbers_success(monkeypatch):
    client = TestClient(app)

    sample_data = {"totalpris": 4_500_000, "fellesgjeld": 125_000}
    monkeypatch.setattr(
        "apps.api.main.scrape_finn_key_numbers",
        lambda url: sample_data,
    )

    response = client.post("/finn/key-numbers", json={"finnkode": "123456"})
    assert response.status_code == 200

    payload = response.json()
    assert payload["finnkode"] == "123456"
    assert payload["url"].endswith("123456")
    assert payload["available"] is True
    assert payload["key_numbers"] == sample_data


def test_finn_key_numbers_with_url_and_no_values(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "apps.api.main.scrape_finn_key_numbers",
        lambda url: {"totalpris": None, "fellesgjeld": None},
    )

    finn_url = "https://www.finn.no/realestate/homes/ad.html?finnkode=789012"
    response = client.post("/finn/key-numbers", json={"url": finn_url})
    assert response.status_code == 200

    payload = response.json()
    assert payload["finnkode"] == "789012"
    assert payload["url"] == finn_url
    assert payload["available"] is False
    assert payload["key_numbers"] == {"totalpris": None, "fellesgjeld": None}


def test_finn_key_numbers_requires_identifier():
    client = TestClient(app)

    response = client.post("/finn/key-numbers", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == "Oppgi enten URL eller finnkode."


def test_finn_key_numbers_rejects_invalid_finnkode():
    client = TestClient(app)

    response = client.post("/finn/key-numbers", json={"finnkode": "ABC123"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Ugyldig finnkode."


def test_finn_key_numbers_handles_http_error(monkeypatch):
    client = TestClient(app)

    error_response = requests.Response()
    error_response.status_code = 404
    http_error = requests.HTTPError("not found", response=error_response)

    def _raise_error(url: str):
        raise http_error

    monkeypatch.setattr("apps.api.main.scrape_finn_key_numbers", _raise_error)

    response = client.post("/finn/key-numbers", json={"finnkode": "555666"})
    assert response.status_code == 502
    assert "Kunne ikke hente nøkkeltall" in response.json()["detail"]
