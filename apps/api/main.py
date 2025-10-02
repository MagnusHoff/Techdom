from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from apps.api import runtime
from techdom.domain.analysis_service import (
    AnalysisDecisionContext,
    compute_analysis,
    normalise_params,
)
from techdom.ingestion.fetch import fetch_prospectus_from_finn, save_pdf_locally
from techdom.services.prospect_jobs import ProspectJobService

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()

app = FastAPI(title="Boliganalyse API (MVP)")
job_service = ProspectJobService()


def _model_dump(model: Optional[Any]) -> Optional[Dict[str, Any]]:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


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
def analyze(req: AnalyzeReq, bg: BackgroundTasks):
    job = job_service.create(req.finnkode)
    job_id = job.id
    finn_url = f"https://www.finn.no/realestate/homes/ad.html?finnkode={req.finnkode}"

    def _run():
        try:
            job_service.mark_running(
                job_id,
                progress=10,
                message="Henter prospekt",
            )
            pdf_bytes, pdf_url, debug = fetch_prospectus_from_finn(finn_url)
            if debug:
                job_service.attach_debug(job_id, debug)
            if not pdf_bytes:
                job_service.mark_failed(job_id, "Fant ikke PDF")
                return
            path = save_pdf_locally(req.finnkode, pdf_bytes)
            job_service.mark_done(job_id, pdf_path=path, pdf_url=pdf_url)
        except Exception as exc:  # pragma: no cover - defensive logging
            job_service.mark_failed(job_id, repr(exc))

    bg.add_task(_run)
    return {"job_id": job_id, "status": "queued"}


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


__all__ = ["app"]
