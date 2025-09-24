# core/drivers/kalandpartners.py
from __future__ import annotations
import re, requests
from typing import Tuple, Dict, Any, Optional
from core.http_headers import BROWSER_HEADERS
from ..config import SETTINGS

PDF_MAGIC = b"%PDF-"


class KalandPartnersDriver:
    name = "kalandpartners"

    def matches(self, url: str) -> bool:
        u = (url or "").lower()
        return "partners.no/eiendom/" in u

    def try_fetch(
        self, sess: requests.Session, page_url: str
    ) -> Tuple[bytes | None, str | None, dict]:
        dbg: Dict[str, Any] = {"driver": self.name, "step": "start", "meta": {}}

        try:
            r = sess.get(
                page_url,
                headers=BROWSER_HEADERS,
                timeout=SETTINGS.REQ_TIMEOUT,
                allow_redirects=True,
            )
            r.raise_for_status()
            html = r.text or ""
            dbg["meta"]["page_len"] = len(html)
        except Exception as e:
            dbg["step"] = f"page_fetch_error:{type(e).__name__}"
            dbg["error"] = repr(e)
            return None, None, dbg

        # Regex som matcher nedlastingslenken
        m = re.search(
            r"https://[^\"']+?/wngetfile\.ashx[^\s\"']+",
            html,
            re.I,
        )
        pdf_url: Optional[str] = None
        if m:
            pdf_url = m.group(0).rstrip("\\")
            dbg["meta"]["pdf_url_found"] = pdf_url

        if not pdf_url:
            dbg["step"] = "no_pdf_found"
            dbg["meta"]["html_dump"] = "data/debug/kalandpartners_last.html"
            try:
                with open(dbg["meta"]["html_dump"], "w") as f:
                    f.write(html)
            except Exception:
                pass
            return None, None, dbg

        try:
            resp = sess.get(
                pdf_url,
                headers={
                    **BROWSER_HEADERS,
                    "Accept": "application/pdf,application/octet-stream,*/*",
                    "Referer": page_url,
                    "Origin": "https://partners.no",
                },
                timeout=SETTINGS.REQ_TIMEOUT,
                allow_redirects=True,
            )
            if resp.ok and resp.content.startswith(PDF_MAGIC):
                dbg["step"] = "ok_from_meglervisning"
                return resp.content, pdf_url, dbg
            else:
                dbg["step"] = "not_pdf"
                dbg["status"] = resp.status_code
                dbg["content_type"] = resp.headers.get("Content-Type")
                return None, None, dbg
        except Exception as e:
            dbg["step"] = f"pdf_fetch_error:{type(e).__name__}"
            dbg["error"] = repr(e)
            return None, None, dbg
