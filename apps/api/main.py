from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Union
from urllib.parse import parse_qs, urlparse

import base64

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import requests
try:
    from pydantic import BaseModel, EmailStr, Field, field_validator
except ImportError:  # pragma: no cover - compatibility with Pydantic v1
    from pydantic import BaseModel, EmailStr, Field, validator as field_validator  # type: ignore[assignment]

from apps.api import runtime

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()

from apps.api.routes import analyses_router, auth_router, stripe_router
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
from techdom.services.salgsoppgave import retrieve_salgsoppgave
from techdom.infrastructure.db import ensure_auth_schema, init_models
from techdom.ingestion.fetch import extract_pdf_text_from_bytes
from techdom.processing.ai import analyze_prospectus
from techdom.processing.tg_extract import (
    ExtractionError as TGExtractionError,
    coerce_tg_strings,
    extract_tg_from_pdf_bytes,
    format_tg_entries,
    merge_tg_lists,
    summarize_tg_entries,
    summarize_tg_strings,
)

app = FastAPI(title="Boliganalyse API (MVP)")
job_service = ProspectJobService()
LOGGER = logging.getLogger(__name__)

_PROSPEKT_DIR = Path("data/cache/prospekt")
app.mount(
    "/files",
    StaticFiles(directory=str(_PROSPEKT_DIR), check_dir=False),
    name="files",
)


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
app.include_router(analyses_router)
app.include_router(stripe_router)


class ProspectusManualReq(BaseModel):
    text: str = ""


class ProspectusManualResp(BaseModel):
    summary_md: str
    tg3: List[str]
    tg2: List[str]
    upgrades: List[str]
    watchouts: List[str]
    questions: List[str]
    tg3_details: List[Dict[str, str]] = Field(default_factory=list)
    tg2_details: List[Dict[str, str]] = Field(default_factory=list)
    tg_markdown: Optional[str] = None
    tg_missing_components: List[str] = Field(default_factory=list)


class ProspectusManualUploadReq(BaseModel):
    filename: Optional[str] = None
    mime: Optional[str] = None
    data: str


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


class SalgsoppgaveResp(BaseModel):
    status: Literal["found", "not_found", "uncertain"]
    original_pdf_url: Optional[str] = None
    stable_pdf_url: Optional[str] = None
    filesize_bytes: Optional[int] = None
    sha256: Optional[str] = None
    confidence: float = 0.0
    log: List[str] = Field(default_factory=list)


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


@app.post("/prospectus/manual", response_model=ProspectusManualResp)
def prospectus_manual(payload: ProspectusManualReq) -> ProspectusManualResp:
    try:
        result = analyze_prospectus(payload.text or "")
    except Exception as exc:  # pragma: no cover - defensive catch
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Kunne ikke analysere salgsoppgaven.",
        ) from exc

    return _build_prospectus_manual_response(result)




def _coerce_detail_list(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, Iterable):
        return []
    details: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label") or "").strip()
        detail = str(item.get("detail") or "").strip()
        source = str(item.get("source") or "").strip()
        if not label or not detail:
            continue
        payload: Dict[str, str] = {"label": label, "detail": detail}
        if source:
            payload["source"] = source
        details.append(payload)
    return details

def _build_prospectus_manual_response(result: Dict[str, Any]) -> ProspectusManualResp:
    if not isinstance(result, dict):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Kunne ikke analysere salgsoppgaven.",
        )

    summary = str(result.get("summary_md") or "")
    tg3 = [str(item) for item in (result.get("tg3") or [])]
    tg2 = [str(item) for item in (result.get("tg2") or [])]
    upgrades = [str(item) for item in (result.get("upgrades") or [])]
    watchouts = [str(item) for item in (result.get("watchouts") or [])]
    questions = [str(item) for item in (result.get("questions") or [])]
    tg2_details = _coerce_detail_list(result.get("tg2_details"))
    tg3_details = _coerce_detail_list(result.get("tg3_details"))
    tg_markdown = str(result.get("tg_markdown") or "").strip() or None
    missing_components = coerce_tg_strings(result.get("tg_missing_components"))

    if not tg2_details and tg2:
        tg2_details = summarize_tg_strings(tg2, level=2)
    if not tg3_details and tg3:
        tg3_details = summarize_tg_strings(tg3, level=3)

    return ProspectusManualResp(
        summary_md=summary,
        tg3=tg3,
        tg2=tg2,
        upgrades=upgrades,
        watchouts=watchouts,
        questions=questions,
        tg3_details=tg3_details,
        tg2_details=tg2_details,
        tg_markdown=tg_markdown,
        tg_missing_components=missing_components,
    )


def _decode_pdf_base64(payload: ProspectusManualUploadReq) -> bytes:
    raw = payload.data.strip()
    if "," in raw and raw.lower().strip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Kunne ikke lese den opplastede filen.") from exc


def _looks_like_pdf(payload: ProspectusManualUploadReq, data: bytes) -> bool:
    if payload.mime and "pdf" in payload.mime.lower():
        return True
    filename = (payload.filename or "").lower()
    if filename.endswith(".pdf"):
        return True
    return data[:4] == b"%PDF"


@app.post("/prospectus/manual/upload", response_model=ProspectusManualResp)
def prospectus_manual_upload(payload: ProspectusManualUploadReq) -> ProspectusManualResp:
    pdf_bytes = _decode_pdf_base64(payload)
    if not pdf_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Filen er tom.",
        )
    if not _looks_like_pdf(payload, pdf_bytes):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Filen må være en PDF.",
        )
    try:
        text = extract_pdf_text_from_bytes(pdf_bytes)
    except Exception as exc:  # pragma: no cover - defensive catch
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Kunne ikke lese PDF-filen.",
        ) from exc

    if not text.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Fant ingen lesbar tekst i PDF-filen.",
        )

    tg_extract_result: Optional[Dict[str, Any]] = None
    tg_extract_json: Optional[Dict[str, Any]] = None
    tg2_from_extract: List[str] = []
    tg3_from_extract: List[str] = []
    tg2_detail_entries: List[Dict[str, str]] = []
    tg3_detail_entries: List[Dict[str, str]] = []
    tg_markdown: Optional[str] = None

    try:
        tg_extract_result = extract_tg_from_pdf_bytes(pdf_bytes)
    except TGExtractionError as exc:
        LOGGER.info("TG-ekstraksjon feilet for opplastet PDF: %s", exc)
    except Exception:  # pragma: no cover - defensive logging
        LOGGER.exception("Uventet feil ved TG-ekstraksjon for opplastet PDF")
    else:
        if isinstance(tg_extract_result, dict):
            candidate_json = tg_extract_result.get("json")
            if isinstance(candidate_json, dict):
                tg_extract_json = candidate_json
                tg2_data = candidate_json.get("TG2") or []
                tg3_data = candidate_json.get("TG3") or []
                if isinstance(tg2_data, Iterable):
                    tg2_detail_entries = summarize_tg_entries(
                        tg2_data,
                        level=2,
                        include_source=True,
                        limit=8,
                    )
                    tg2_from_extract = format_tg_entries(
                        tg2_data,
                        level=2,
                        include_component=True,
                        include_source=True,
                        limit=8,
                    )
                if isinstance(tg3_data, Iterable):
                    tg3_detail_entries = summarize_tg_entries(
                        tg3_data,
                        level=3,
                        include_source=True,
                        limit=6,
                    )
                    tg3_from_extract = format_tg_entries(
                        tg3_data,
                        level=3,
                        include_component=True,
                        include_source=True,
                        limit=6,
                    )
            markdown_candidate = tg_extract_result.get("markdown")
            if isinstance(markdown_candidate, str) and markdown_candidate.strip():
                tg_markdown = markdown_candidate.strip()

    try:
        result = analyze_prospectus(text)
    except Exception as exc:  # pragma: no cover - defensive catch
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Kunne ikke analysere salgsoppgaven.",
        ) from exc

    result_dict: Dict[str, Any] = dict(result) if isinstance(result, dict) else {}

    if tg_extract_json is not None:
        existing_tg2 = coerce_tg_strings(result_dict.get("tg2"))
        existing_tg3 = coerce_tg_strings(result_dict.get("tg3"))
        if tg2_from_extract:
            result_dict["tg2"] = merge_tg_lists(tg2_from_extract, existing_tg2, limit=8)
        elif existing_tg2:
            result_dict["tg2"] = existing_tg2
        if tg3_from_extract:
            result_dict["tg3"] = merge_tg_lists(tg3_from_extract, existing_tg3, limit=6)
        elif existing_tg3:
            result_dict["tg3"] = existing_tg3

        if tg2_detail_entries:
            result_dict["tg2_details"] = tg2_detail_entries
        elif existing_tg2:
            result_dict["tg2_details"] = summarize_tg_strings(existing_tg2, level=2)

        if tg3_detail_entries:
            result_dict["tg3_details"] = tg3_detail_entries
        elif existing_tg3:
            result_dict["tg3_details"] = summarize_tg_strings(existing_tg3, level=3)

        result_dict["tg_extract"] = tg_extract_json
        missing_candidates = tg_extract_json.get("missing")
        missing_list = coerce_tg_strings(missing_candidates)
        if missing_list:
            result_dict["tg_missing_components"] = missing_list

    if tg_markdown and not result_dict.get("tg_markdown"):
        result_dict["tg_markdown"] = tg_markdown

    if "tg2_details" not in result_dict:
        fallback_tg2 = coerce_tg_strings(result_dict.get("tg2"))
        if fallback_tg2:
            result_dict["tg2_details"] = summarize_tg_strings(fallback_tg2, level=2)

    if "tg3_details" not in result_dict:
        fallback_tg3 = coerce_tg_strings(result_dict.get("tg3"))
        if fallback_tg3:
            result_dict["tg3_details"] = summarize_tg_strings(fallback_tg3, level=3)

    return _build_prospectus_manual_response(result_dict)


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


@app.get("/salgsoppgave", response_model=SalgsoppgaveResp)
async def salgsoppgave_lookup(
    finn: str = Query(..., alias="finn"),
    extra: List[str] = Query(default=[]),
) -> SalgsoppgaveResp:
    try:
        result = await retrieve_salgsoppgave(finn, extra_terms=extra)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    payload = result.to_dict()
    payload.setdefault("log", [])
    return SalgsoppgaveResp(**payload)


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
