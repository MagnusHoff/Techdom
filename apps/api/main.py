from __future__ import annotations

import os

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from apps.api import runtime
from techdom.ingestion.fetch import fetch_prospectus_from_finn, save_pdf_locally
from techdom.services.prospect_jobs import ProspectJobService

_bootstrap = runtime.ensure_bootstrap()
runtime.load_environment()

app = FastAPI(title="Boliganalyse API (MVP)")
job_service = ProspectJobService()


class AnalyzeReq(BaseModel):
    finnkode: str


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
