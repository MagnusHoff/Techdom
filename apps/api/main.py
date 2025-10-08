from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import requests
try:
    from pydantic import BaseModel, EmailStr, Field, field_validator
except ImportError:  # pragma: no cover - compatibility with Pydantic v1
    from pydantic import BaseModel, EmailStr, Field, validator as field_validator  # type: ignore[assignment]

from apps.api import runtime

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()

from apps.api.routes import auth_router
from techdom.services.feedback import (
    FeedbackConfigError,
    FeedbackDeliveryError,
    FeedbackMailConfig,
    send_feedback_email,
)
from techdom.ingestion.scrape import scrape_finn_key_numbers
from techdom.domain.analysis_service import (
    AnalysisDecisionContext,
    compute_analysis,
    normalise_params,
)
from techdom.domain.history import get_total_count
from techdom.services.prospect_jobs import ProspectJobService
from techdom.infrastructure.db import ensure_auth_schema, init_models

app = FastAPI(title="Boliganalyse API (MVP)")
job_service = ProspectJobService()


@app.on_event("startup")
async def _startup() -> None:
    await init_models()
    await ensure_auth_schema()


def _model_dump(value: Optional[Any]) -> Optional[Dict[str, Any]]:
    """Return a serialisable dict for Pydantic/BaseModel values or passthrough others."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()  # type: ignore[no-any-return]
    if hasattr(value, "dict"):
        return value.dict()  # type: ignore[no-any-return]
    return value  # type: ignore[return-value]


def _cors_origins() -> list[str]:
    raw = os.getenv("API_CORS_ORIGINS")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "https://techdom-frontend.onrender.com",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


class AnalysisReq(BaseModel):
    price: Union[int, float, str]
    equity: Union[int, float, str]
    interest: Union[int, float, str]
    term_years: Union[int, float, str] = 30
    rent: Union[int, float, str] = 0
    hoa: Union[int, float, str] = 0
    maint_pct: Union[int, float, str] = 0
    vacancy_pct: Union[int, float, str] = 0
    other_costs: Union[int, float, str] = 0
    tg2_items: List[str] = Field(default_factory=list)
    tg3_items: List[str] = Field(default_factory=list)
    tg_data_available: Optional[bool] = None
    upgrades: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    bath_age_years: Optional[float] = None
    kitchen_age_years: Optional[float] = None
    roof_age_years: Optional[float] = None

    def to_params(self) -> Dict[str, Any]:
        return {
            "price": self.price,
            "equity": self.equity,
            "interest": self.interest,
            "term_years": self.term_years,
            "rent": self.rent,
            "hoa": self.hoa,
            "maint_pct": self.maint_pct,
            "vacancy_pct": self.vacancy_pct,
            "other_costs": self.other_costs,
        }

    def decision_context(self) -> AnalysisDecisionContext:
        available = (
            self.tg_data_available
            if self.tg_data_available is not None
            else bool(self.tg2_items or self.tg3_items)
        )
        return AnalysisDecisionContext(
            tg2_items=self.tg2_items,
            tg3_items=self.tg3_items,
            tg_data_available=available,
            upgrades_recent=self.upgrades,
            warnings=self.warnings,
            bath_age_years=self.bath_age_years,
            kitchen_age_years=self.kitchen_age_years,
            roof_age_years=self.roof_age_years,
        )


class AnalysisResp(BaseModel):
    input_params: Dict[str, Any]
    normalised_params: Dict[str, Any]
    metrics: Dict[str, Any]
    calculated_metrics: Optional[Dict[str, Any]]
    decision_result: Optional[Dict[str, Any]]
    decision_ui: Dict[str, Any]
    ai_text: str


class AnalyzeReq(BaseModel):
    finnkode: str


class FinnKeyNumbersReq(BaseModel):
    finnkode: Optional[str] = None
    url: Optional[str] = None


class StatsResp(BaseModel):
    total_analyses: int


class FeedbackCategory(str, Enum):
    IDEA = "idea"
    PROBLEM = "problem"
    OTHER = "other"


FEEDBACK_CATEGORY_LABELS: dict[FeedbackCategory, str] = {
    FeedbackCategory.IDEA: "Idé",
    FeedbackCategory.PROBLEM: "Problem",
    FeedbackCategory.OTHER: "Annet",
}


class FeedbackPayload(BaseModel):
    category: FeedbackCategory
    message: str = Field(..., min_length=1, max_length=5000)
    email: Optional[EmailStr] = None

    @field_validator("message")
    def _strip_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Meldingen kan ikke være tom.")
        return cleaned


@app.post("/analysis", response_model=AnalysisResp)
def analysis(req: AnalysisReq) -> AnalysisResp:
    params = req.to_params()
    analysis_result = compute_analysis(params, req.decision_context())
    normalised = normalise_params(params)
    return AnalysisResp(
        input_params=params,
        normalised_params=normalised,
        metrics=analysis_result.metrics,
        calculated_metrics=_model_dump(analysis_result.calculated_metrics),
        decision_result=_model_dump(analysis_result.decision_result),
        decision_ui=analysis_result.decision_ui,
        ai_text=analysis_result.ai_text,
    )


@app.post("/analyze")
def analyze(req: AnalyzeReq):
    finn_url = f"https://www.finn.no/realestate/homes/ad.html?finnkode={req.finnkode}"
    job = job_service.create(
        req.finnkode,
        payload={"finnkode": req.finnkode, "finn_url": finn_url},
        enqueue=True,
    )
    return {"job_id": job.id, "status": job.status}


def _build_finn_url(finnkode: str) -> str:
    return f"https://www.finn.no/realestate/homes/ad.html?finnkode={finnkode}"


def _extract_finnkode_from_url(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        candidate = params.get("finnkode")
        if not candidate:
            return None
        value = (candidate[0] or "").strip()
        return value if value.isdigit() else None
    except Exception:
        return None


def _has_key_number_values(data: Dict[str, Any]) -> bool:
    for value in data.values():
        if value not in (None, "", [], {}):
            return True
    return False


@app.post("/finn/key-numbers")
def finn_key_numbers(payload: FinnKeyNumbersReq):
    raw_finnkode = (payload.finnkode or "").strip()
    raw_url = (payload.url or "").strip()

    if not raw_url and not raw_finnkode:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Oppgi enten URL eller finnkode.")

    finnkode: Optional[str] = None
    url: str

    if raw_url:
        url = raw_url
        finnkode = _extract_finnkode_from_url(raw_url)
    else:
        if not raw_finnkode.isdigit():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ugyldig finnkode.")
        finnkode = raw_finnkode
        url = _build_finn_url(raw_finnkode)

    try:
        response = scrape_finn_key_numbers(url, include_raw=True)
        if isinstance(response, tuple):
            key_numbers, raw_facts = response
        else:  # pragma: no cover - fallback for unexpected return type
            key_numbers = response
            raw_facts = []
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        detail = f"Kunne ikke hente nøkkeltall fra FINN (HTTP {code})." if code else "Kunne ikke hente nøkkeltall fra FINN."
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail) from exc
    except requests.RequestException as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Kunne ikke hente nøkkeltall fra FINN.") from exc

    available = _has_key_number_values(key_numbers)

    return {
        "finnkode": finnkode,
        "url": url,
        "available": available,
        "key_numbers": key_numbers,
        "key_facts_raw": raw_facts,
        "keyFactsRaw": raw_facts,
    }


@app.get("/status/{job_id}")
def get_job_status(job_id: str):
    job = job_service.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return job


def _prospekt_path_for(finnkode: str) -> Path:
    if not finnkode.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ugyldig finnkode")
    return Path("data/cache/prospekt") / f"{finnkode}.pdf"


@app.get("/pdf/{finnkode}")
def pdf_link(finnkode: str):
    path = _prospekt_path_for(finnkode)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not ready")
    return {"path": str(path), "url": f"/files/{finnkode}.pdf"}


@app.get("/files/{finnkode}.pdf")
def download_prospect(finnkode: str):
    path = _prospekt_path_for(finnkode)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not ready")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{finnkode}.pdf",
    )


@app.get("/stats", response_model=StatsResp)
def stats() -> StatsResp:
    total = get_total_count()
    return StatsResp(total_analyses=total)


@app.post("/feedback", status_code=status.HTTP_202_ACCEPTED)
def feedback(payload: FeedbackPayload) -> dict[str, str]:
    try:
        mail_config = FeedbackMailConfig.from_env()
    except FeedbackConfigError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    category_label = FEEDBACK_CATEGORY_LABELS[payload.category]
    subject = f"Ny tilbakemelding: {category_label}"
    lines = [f"Kategori: {category_label}", "", payload.message]
    if payload.email:
        lines.extend(["", f"E-post fra bruker: {payload.email}"])

    body = "\n".join(lines)

    try:
        send_feedback_email(subject, body, reply_to=payload.email, config=mail_config)
    except FeedbackDeliveryError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Kunne ikke sende tilbakemeldingen.") from exc

    return {"status": "sent"}
