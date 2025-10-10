from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests import RequestException

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
from techdom.processing.tg_extract import (
    ExtractionError as TGExtractionError,
    coerce_tg_strings,
    extract_tg_from_pdf_bytes,
    format_tg_entries,
    merge_tg_lists,
    summarize_tg_entries,
    summarize_tg_strings,
)
from techdom.ingestion.fetch import (
    extract_pdf_text_from_bytes,
    fetch_prospectus_from_finn,
    save_pdf_locally,
)
from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.ingestion.scrape import scrape_finn, scrape_finn_key_numbers
from techdom.services.prospect_jobs import ProspectJob, ProspectJobService


LOGGER = logging.getLogger(__name__)


_PDF_HEAD_TIMEOUT = 12


def _local_pdf_url(finnkode: str, pdf_path: Optional[str]) -> Optional[str]:
    if not pdf_path:
        return None
    stem = Path(pdf_path).stem or finnkode
    base = os.getenv("PUBLIC_API_BASE_URL") or os.getenv("NEXT_PUBLIC_API_BASE_URL")
    if base:
        return f"{base.rstrip('/')}/files/{stem}.pdf"
    return f"/files/{stem}.pdf"


def _verify_pdf_head(url: str, *, referer: Optional[str] = None) -> Tuple[bool, Optional[str], float, bool]:
    """
    Returnerer (ok, final_url, confidence, protected)
    protected=True indikerer at vi traff innlogging (401/403).
    """
    headers: Dict[str, str] = {**BROWSER_HEADERS, "Accept": "application/pdf"}
    if referer:
        headers["Referer"] = referer
        try:
            parsed = requests.utils.urlparse(referer)
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            pass
    try:
        response = requests.head(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=_PDF_HEAD_TIMEOUT,
        )
    except RequestException:
        return False, None, 0.0, False

    status = response.status_code
    if status in (401, 403):
        return False, None, 0.0, True
    if status >= 400:
        return False, None, 0.0, False

    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type != "application/pdf":
        return False, None, 0.0, False

    final_url = str(response.url) if getattr(response, "url", None) else url
    confidence = 0.8
    if final_url.lower().endswith(".pdf"):
        confidence = 1.0
    return True, final_url, confidence, False


def _build_salgsoppgave_links(
    *,
    finnkode: str,
    finn_url: str,
    fetch_debug: Optional[Dict[str, Any]],
    pdf_url: Optional[str],
    pdf_path: Optional[str],
) -> Dict[str, Any]:
    links: Dict[str, Any] = {"salgsoppgave_pdf": None, "confidence": 0.0}
    seen: set[str] = set()
    message: Optional[str] = None
    protected = False

    def _add(url: Optional[str], referer: Optional[str], base_confidence: float) -> None:
        if not url or not isinstance(url, str):
            return
        candidate = url.strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        candidates.append((candidate, referer, base_confidence))

    candidates: list[tuple[str, Optional[str], float]] = []
    dbg = fetch_debug or {}
    _add(dbg.get("pdf_url"), dbg.get("finn_url") or finn_url, 0.95)
    _add(dbg.get("presigned_url"), None, 0.8)
    _add(dbg.get("stable_url"), None, 0.98)
    _add(pdf_url, None, 0.95)

    for url, referer, base_conf in candidates:
        ok, final_url, confidence, is_protected = _verify_pdf_head(url, referer=referer)
        if is_protected:
            protected = True
            message = "Beskyttet – last ned lokalt."
            continue
        if not ok or not final_url:
            continue
        links["salgsoppgave_pdf"] = final_url
        links["confidence"] = round(min(1.0, max(base_conf, confidence)), 3)
        if message:
            links["message"] = message
        return links

    if pdf_path:
        local_url = _local_pdf_url(finnkode, pdf_path)
        if local_url:
            links["salgsoppgave_pdf"] = local_url
            # Keep moderate confidence so the local copy is offered even for protected sources.
            links["confidence"] = 0.6 if not protected else 0.5
            if message:
                links["message"] = message
            return links

    if protected:
        links["confidence"] = 0.0
        links["salgsoppgave_pdf"] = None
        links["message"] = message or "Beskyttet – last ned lokalt."
        return links

    if pdf_path and not links.get("salgsoppgave_pdf"):
        fallback_url = _local_pdf_url(finnkode, pdf_path)
        if fallback_url:
            links["salgsoppgave_pdf"] = fallback_url
            links["confidence"] = max(links.get("confidence", 0.0), 0.5)
            if message:
                links["message"] = message
            return links

    if message:
        links["message"] = message

    return links


def _model_dump(value: Optional[Any]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()  # type: ignore[no-any-return]
    if hasattr(value, "dict"):
        return value.dict()  # type: ignore[no-any-return]
    return value  # type: ignore[return-value]


def _is_missing_amount(value: Any) -> bool:
    """
    Return True if the incoming value is effectively missing (None or empty string).
    Numeric zero is treated as a legitimate value.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _enrich_listing_with_key_numbers(
    *,
    finnkode: str,
    finn_url: str,
    listing: Dict[str, Any],
    need_price: bool,
    need_hoa: bool,
) -> Optional[Dict[str, Any]]:
    if not (need_price or need_hoa):
        return None

    try:
        key_numbers_response = scrape_finn_key_numbers(finn_url, include_raw=True)
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.debug("Kunne ikke hente nøkkeltall fra FINN for %s: %s", finnkode, exc)
        return None

    if isinstance(key_numbers_response, tuple):
        key_numbers, raw_facts = key_numbers_response
    else:  # pragma: no cover - legacy compatibility
        key_numbers = key_numbers_response
        raw_facts = []

    key_numbers = key_numbers if isinstance(key_numbers, dict) else {}
    raw_facts = raw_facts if isinstance(raw_facts, list) else []

    applied: Dict[str, bool] = {}

    if need_price:
        candidate_price = (
            key_numbers.get("totalpris")
            or key_numbers.get("prisantydning")
            or key_numbers.get("total_price")
        )
        price_int = as_int(candidate_price, 0)
        if price_int > 0:
            listing["total_price"] = price_int
            listing.setdefault("totalpris", price_int)
            listing.setdefault("prisantydning", price_int)
            applied["price"] = True

    if need_hoa:
        candidate_hoa = (
            key_numbers.get("felleskostnader")
            or key_numbers.get("felleskost")
            or key_numbers.get("felleskostnad")
        )
        if candidate_hoa is not None and candidate_hoa != "":
            hoa_int = as_int(candidate_hoa, 0)
            listing["hoa_month"] = hoa_int
            listing.setdefault("felleskostnader", hoa_int)
            applied["hoa"] = True

    if raw_facts:
        listing.setdefault("keyFactsRaw", raw_facts)
        listing.setdefault("key_facts_raw", raw_facts)

    payload: Dict[str, Any] = {}
    if key_numbers:
        payload["values"] = key_numbers
    if raw_facts:
        payload["raw_facts"] = raw_facts
    if applied:
        payload["applied"] = applied
        LOGGER.info(
            "Brukte FINN-nøkkeltall for %s (pris:%s, felleskost:%s)",
            finnkode,
            "ja" if applied.get("price") else "nei",
            "ja" if applied.get("hoa") else "nei",
        )

    return payload if payload else None


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

            price_missing = _is_missing_amount(listing_info.get("total_price")) and _is_missing_amount(
                listing_info.get("totalpris")
            )
            hoa_missing = _is_missing_amount(listing_info.get("hoa_month")) and _is_missing_amount(
                listing_info.get("felleskostnader")
            )

            finn_key_numbers_payload = _enrich_listing_with_key_numbers(
                finnkode=finnkode,
                finn_url=finn_url,
                listing=listing_info,
                need_price=price_missing,
                need_hoa=hoa_missing,
            )

            self.job_service.store_artifact(job_id, "listing", listing_info)
            if finn_key_numbers_payload:
                self.job_service.store_artifact(job_id, "finn_key_numbers", finn_key_numbers_payload)

            price = as_int(
                listing_info.get("total_price")
                or listing_info.get("totalpris")
                or listing_info.get("prisantydning"),
                0,
            )
            hoa = as_int(
                listing_info.get("hoa_month")
                or listing_info.get("felleskostnader")
                or listing_info.get("felleskost")
                or listing_info.get("felleskostnad"),
                0,
            )
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
            fetch_debug: Optional[Dict[str, Any]]
            try:
                pdf_bytes, pdf_url, fetch_debug = fetch_prospectus_from_finn(finn_url)
            except Exception as fetch_exc:  # pragma: no cover - defensive fallback
                LOGGER.warning(
                    "Fetch av salgsoppgave feilet for %s – fortsetter uten PDF: %s",
                    finnkode,
                    fetch_exc,
                )
                pdf_bytes = None
                pdf_url = None
                fetch_debug = {
                    "step": "exception",
                    "error": f"{type(fetch_exc).__name__}: {fetch_exc}",
                }
            if fetch_debug:
                self.job_service.attach_debug(job_id, {"fetch": fetch_debug})

            pdf_path: Optional[str] = None
            pdf_text: Optional[str] = None
            ai_extract: Dict[str, Any] = {}
            excerpt = ""
            links_payload: Dict[str, Any]
            completion_message = "Analyse fullført"
            tg_extract_payload: Optional[Dict[str, Any]] = None
            tg_markdown: Optional[str] = None
            tg2_from_extract: List[str] = []
            tg3_from_extract: List[str] = []
            tg2_detail_entries: List[Dict[str, str]] = []
            tg3_detail_entries: List[Dict[str, str]] = []

            if pdf_bytes:
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

                links_payload = _build_salgsoppgave_links(
                    finnkode=finnkode,
                    finn_url=finn_url,
                    fetch_debug=fetch_debug,
                    pdf_url=pdf_url,
                    pdf_path=pdf_path,
                )
                self.job_service.store_artifact(job_id, "links", links_payload)

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
                raw_ai_extract = analyze_prospectus(pdf_text or "") if pdf_text else {}
                ai_extract = dict(raw_ai_extract) if isinstance(raw_ai_extract, dict) else {}

                try:
                    tg_result = extract_tg_from_pdf_bytes(pdf_bytes)
                except TGExtractionError as exc:
                    LOGGER.info("TG-ekstraksjon feilet for %s: %s", finnkode, exc)
                except Exception:
                    LOGGER.exception("Uventet feil ved TG-ekstraksjon for %s", finnkode)
                else:
                    if isinstance(tg_result, dict):
                        candidate_json = tg_result.get("json")
                        if isinstance(candidate_json, dict):
                            tg_extract_payload = candidate_json
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
                        markdown_candidate = tg_result.get("markdown")
                        if isinstance(markdown_candidate, str) and markdown_candidate.strip():
                            tg_markdown = markdown_candidate.strip()
            else:
                completion_message = "Analyse fullført (uten salgsoppgave)"
                self.job_service.store_artifact(
                    job_id,
                    "pdf_meta",
                    {
                        "path": None,
                        "url": pdf_url,
                        "bytes": 0,
                    },
                )
                links_payload = _build_salgsoppgave_links(
                    finnkode=finnkode,
                    finn_url=finn_url,
                    fetch_debug=fetch_debug,
                    pdf_url=pdf_url,
                    pdf_path=None,
                )
                self.job_service.store_artifact(job_id, "links", links_payload)
                self.job_service.mark_running(
                    job_id,
                    progress=55,
                    message="Fant ikke salgsoppgave – bruker annonse-data",
                )
                self.job_service.store_artifact(
                    job_id,
                    "pdf_text_excerpt",
                    {
                        "length": 0,
                        "excerpt": "",
                    },
                )

            if isinstance(ai_extract, dict):
                existing_tg2 = coerce_tg_strings(ai_extract.get("tg2"))
                existing_tg3 = coerce_tg_strings(ai_extract.get("tg3"))
                if tg2_from_extract:
                    ai_extract["tg2"] = merge_tg_lists(tg2_from_extract, existing_tg2, limit=8)
                elif existing_tg2:
                    ai_extract["tg2"] = existing_tg2
                if tg3_from_extract:
                    ai_extract["tg3"] = merge_tg_lists(tg3_from_extract, existing_tg3, limit=6)
                elif existing_tg3:
                    ai_extract["tg3"] = existing_tg3

                if tg2_detail_entries:
                    ai_extract["tg2_details"] = tg2_detail_entries
                elif existing_tg2:
                    ai_extract["tg2_details"] = summarize_tg_strings(existing_tg2, level=2)

                if tg3_detail_entries:
                    ai_extract["tg3_details"] = tg3_detail_entries
                elif existing_tg3:
                    ai_extract["tg3_details"] = summarize_tg_strings(existing_tg3, level=3)

                if tg_extract_payload is not None:
                    ai_extract["tg_extract"] = tg_extract_payload
                    missing_candidates = tg_extract_payload.get("missing")
                    missing_list = coerce_tg_strings(missing_candidates)
                    if missing_list:
                        ai_extract["tg_missing_components"] = missing_list
                if tg_markdown and not ai_extract.get("tg_markdown"):
                    ai_extract["tg_markdown"] = tg_markdown

            if tg_extract_payload is not None:
                self.job_service.store_artifact(
                    job_id,
                    "tg_extract",
                    {
                        "json": tg_extract_payload,
                        "markdown": tg_markdown,
                    },
                )

            ai_extract["links"] = links_payload
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
                pdf_url=(
                    links_payload.get("salgsoppgave_pdf")
                    if isinstance(links_payload, dict) and links_payload.get("salgsoppgave_pdf")
                    else _local_pdf_url(finnkode, pdf_path)
                ),
                result={
                    "analysis": analysis_payload,
                    "listing": listing_info,
                    "ai_extract": ai_extract,
                    "rent_estimate": rent_payload,
                    "interest_estimate": interest_payload,
                    "pdf_text_excerpt": excerpt,
                    "links": links_payload,
                    "finn_key_numbers": finn_key_numbers_payload,
                },
                message=completion_message,
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
