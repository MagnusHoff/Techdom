from __future__ import annotations
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uuid, time
from typing import Dict
from core.fetch import fetch_prospectus_from_finn, save_pdf_locally

app = FastAPI(title="Boliganalyse API (MVP)")

# enkel in-memory status (erstattes senere med Redis/Queue)
JOBS: Dict[str, dict] = {}


class AnalyzeReq(BaseModel):
    finnkode: str


@app.post("/analyze")
def analyze(req: AnalyzeReq, bg: BackgroundTasks):
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"state": "queued", "progress": 0, "finnkode": req.finnkode}
    finn_url = f"https://www.finn.no/realestate/homes/ad.html?finnkode={req.finnkode}"

    def _run():
        try:
            JOBS[job_id] = {
                **JOBS[job_id],
                "state": "running",
                "progress": 10,
                "message": "Henter prospekt",
            }
            pdf_bytes, pdf_url, dbg = fetch_prospectus_from_finn(finn_url)
            JOBS[job_id]["debug"] = dbg
            if not pdf_bytes:
                JOBS[job_id]["state"] = "failed"
                JOBS[job_id]["message"] = "Fant ikke PDF"
                return
            path = save_pdf_locally(req.finnkode, pdf_bytes)
            JOBS[job_id]["state"] = "done"
            JOBS[job_id]["progress"] = 100
            JOBS[job_id]["pdf_path"] = path
            JOBS[job_id]["pdf_url"] = pdf_url
        except Exception as e:
            JOBS[job_id]["state"] = "failed"
            JOBS[job_id]["message"] = repr(e)

    bg.add_task(_run)
    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def status(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    return j


@app.get("/pdf/{finnkode}")
def pdf_link(finnkode: str):
    # MVP: returner lokal filsti (prod: signer S3 URL)
    import os

    path = f"data/prospekt/{finnkode}.pdf"
    if not os.path.exists(path):
        raise HTTPException(404, "not ready")
    return {"path": path}
