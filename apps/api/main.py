from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from apps.api import runtime

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()

from apps.api.routes import auth_router
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


class StatsResp(BaseModel):
    total_analyses: int


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


@app.get("/status/{job_id}")
def status(job_id: str):
    job = job_service.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return job


@app.get("/pdf/{finnkode}")
def pdf_link(finnkode: str):
    path = f"data/cache/prospekt/{finnkode}.pdf"
    if not os.path.exists(path):
        raise HTTPException(404, "not ready")
    return {"path": path}


@app.get("/stats", response_model=StatsResp)
def stats() -> StatsResp:
    total = get_total_count()
    return StatsResp(total_analyses=total)
