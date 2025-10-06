from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict, Optional

from techdom.domain.analysis_service import (
    AnalysisDecisionContext,
    as_float,
    as_int,
    as_opt_float,
    compute_analysis,
    default_equity,
    normalise_params,
)
from techdom.processing.ai import analyze_prospectus
from techdom.processing.rates import get_interest_estimate
from techdom.processing.rent.logic import get_rent_by_csv
from techdom.ingestion.fetch import (
    extract_pdf_text_from_bytes,
    fetch_prospectus_from_finn,
    save_pdf_locally,
)
from techdom.ingestion.scrape import scrape_finn
from techdom.services.prospect_jobs import ProspectJob, ProspectJobService


LOGGER = logging.getLogger(__name__)


def _model_dump(value: Optional[Any]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()  # type: ignore[no-any-return]
    if hasattr(value, "dict"):
        return value.dict()  # type: ignore[no-any-return]
    return value  # type: ignore[return-value]


class ProspectAnalysisPipeline:
    """End-to-end pipeline for processing a FINN listing prospect."""

    def __init__(self, job_service: ProspectJobService) -> None:
        self.job_service = job_service

    def run(self, job: ProspectJob) -> None:
        job_id = job.id
        finnkode = job.finnkode
        try:
            finn_url = self._build_finn_url(job)
            self.job_service.update_payload(job_id, {"finn_url": finn_url})

            self.job_service.mark_running(
                job_id,
                progress=5,
                message="Henter annonse-data fra FINN",
            )
            listing_info = scrape_finn(finn_url) or {}
            self.job_service.store_artifact(job_id, "listing", listing_info)

            price = as_int(listing_info.get("total_price"), 0)
            hoa = as_int(listing_info.get("hoa_month"), 0)
            suggested_equity = default_equity(price) if price else 0

            area = as_opt_float(listing_info.get("area_m2"))
            rooms = as_int(listing_info.get("rooms"), 0) or None

            self.job_service.mark_running(
                job_id,
                progress=15,
                message="Estimerer leie fra SSB",
            )
            rent_estimate = get_rent_by_csv(listing_info, area, rooms)
            rent_payload = asdict(rent_estimate) if rent_estimate else None
            if rent_payload:
                rent_suggestion = int(rent_payload.get("gross_rent") or 0)
            else:
                rent_suggestion = 0
            self.job_service.store_artifact(job_id, "rent_estimate", rent_payload)

            self.job_service.mark_running(
                job_id,
                progress=25,
                message="Henter rente-estimat",
            )
            interest_value = get_interest_estimate(return_meta=True)
            if isinstance(interest_value, tuple):
                interest_rate, interest_meta = interest_value
                interest_payload = {
                    "rate": interest_rate,
                    "meta": interest_meta.__dict__ if hasattr(interest_meta, "__dict__") else interest_meta,
                }
            else:
                interest_rate = interest_value
                interest_payload = {"rate": interest_rate}
            self.job_service.store_artifact(job_id, "interest_estimate", interest_payload)

            term_years = as_int(job.payload.get("term_years", 30), 30)
            rent_value = rent_suggestion or as_int(job.payload.get("rent", 0), 0)
            analysis_params: Dict[str, Any] = {
                "price": price,
                "equity": suggested_equity or as_int(job.payload.get("equity", 0), 0),
                "interest": float(interest_rate or as_float(job.payload.get("interest", 0.0), 0.0)),
                "term_years": term_years,
                "rent": rent_value,
                "hoa": hoa or as_int(job.payload.get("hoa", 0), 0),
                "maint_pct": as_float(job.payload.get("maint_pct", 6.0), 6.0),
                "vacancy_pct": as_float(job.payload.get("vacancy_pct", 0.0), 0.0),
                "other_costs": as_int(job.payload.get("other_costs", 0), 0),
            }
            self.job_service.store_artifact(job_id, "analysis_params", analysis_params)

            self.job_service.mark_running(
                job_id,
                progress=40,
                message="Henter salgsoppgave",
            )
            pdf_bytes, pdf_url, fetch_debug = fetch_prospectus_from_finn(finn_url)
            if fetch_debug:
                self.job_service.attach_debug(job_id, {"fetch": fetch_debug})

            if not pdf_bytes:
                self.job_service.mark_failed(job_id, "Fant ikke prospekt PDF")
                return

            try:
                pdf_path = save_pdf_locally(finnkode, pdf_bytes)
            except Exception:
                LOGGER.exception("Klarte ikke å lagre prospekt lokalt for %s", finnkode)
                pdf_path = None

            self.job_service.store_artifact(
                job_id,
                "pdf_meta",
                {
                    "path": pdf_path,
                    "url": pdf_url,
                    "bytes": len(pdf_bytes),
                },
            )

            self.job_service.mark_running(
                job_id,
                progress=55,
                message="Parser salgsoppgave",
            )
            pdf_text = extract_pdf_text_from_bytes(pdf_bytes)
            excerpt = (pdf_text or "")[:2000]
            self.job_service.store_artifact(
                job_id,
                "pdf_text_excerpt",
                {
                    "length": len(pdf_text or ""),
                    "excerpt": excerpt,
                },
            )

            self.job_service.mark_running(
                job_id,
                progress=70,
                message="Kjører AI-analyse",
            )
            ai_extract = analyze_prospectus(pdf_text or "") if pdf_text else {}
            self.job_service.store_artifact(job_id, "ai_extract", ai_extract)

            tg2_items = [
                str(item) for item in ai_extract.get("tg2", [])
            ] if isinstance(ai_extract, dict) else []
            tg3_items = [
                str(item) for item in ai_extract.get("tg3", [])
            ] if isinstance(ai_extract, dict) else []
            upgrades = [
                str(item) for item in ai_extract.get("upgrades", [])
            ] if isinstance(ai_extract, dict) else []
            warnings = [
                str(item) for item in ai_extract.get("watchouts", [])
            ] if isinstance(ai_extract, dict) else []
            ctx = AnalysisDecisionContext(
                tg2_items=tg2_items,
                tg3_items=tg3_items,
                tg_data_available=bool(tg2_items or tg3_items),
                upgrades_recent=upgrades,
                warnings=warnings,
            )

            self.job_service.mark_running(
                job_id,
                progress=85,
                message="Beregner økonomi og score",
            )
            analysis_result = compute_analysis(analysis_params, ctx)
            analysis_payload: Dict[str, Any] = {
                "input_params": analysis_params,
                "normalised_params": normalise_params(analysis_params),
                "metrics": analysis_result.metrics,
                "calculated_metrics": _model_dump(analysis_result.calculated_metrics),
                "decision_result": _model_dump(analysis_result.decision_result),
                "decision_ui": analysis_result.decision_ui,
                "ai_text": analysis_result.ai_text,
            }
            self.job_service.store_artifact(job_id, "analysis", analysis_payload)

            self.job_service.mark_done(
                job_id,
                pdf_path=pdf_path,
                pdf_url=pdf_url,
                result={
                    "analysis": analysis_payload,
                    "listing": listing_info,
                    "ai_extract": ai_extract,
                    "rent_estimate": rent_payload,
                    "interest_estimate": interest_payload,
                    "pdf_text_excerpt": excerpt,
                },
                message="Analyse fullført",
            )
        except Exception as exc:  # pragma: no cover - defensive catch-all
            LOGGER.exception("Prospect pipeline failed for job %s", job.id)
            self.job_service.mark_failed(
                job_id,
                message="Uventet feil i pipeline",
                error=repr(exc),
            )

    def _build_finn_url(self, job: ProspectJob) -> str:
        payload_url = job.payload.get("finn_url")
        if isinstance(payload_url, str) and payload_url:
            return payload_url
        candidate = job.finnkode.strip()
        if candidate.startswith("http"):
            return candidate
        return f"https://www.finn.no/realestate/homes/ad.html?finnkode={candidate}"


__all__ = ["ProspectAnalysisPipeline"]
