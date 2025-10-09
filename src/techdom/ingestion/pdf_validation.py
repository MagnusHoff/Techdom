from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence


_DEFAULT_KEYWORDS = ("salgsoppgave", "prospekt")
_PAGE_SCAN_LIMIT = 2
_MIN_BYTES = 50 * 1024  # 50 kB


@dataclass(frozen=True)
class PdfValidationResult:
    ok: bool
    reason: str | None
    matched_keywords: List[str]
    pages_scanned: int
    bytes_size: int

    @property
    def has_keywords(self) -> bool:
        return bool(self.matched_keywords)


def _extract_pages_with_fitz(pdf_bytes: bytes, max_pages: int) -> Sequence[str]:
    try:
        import fitz  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return ()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return ()

    try:
        upto = min(doc.page_count, max_pages)
        return tuple(doc.load_page(i).get_text("text") or "" for i in range(upto))
    except Exception:
        return ()
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _extract_pages_with_pypdf(pdf_bytes: bytes, max_pages: int) -> Sequence[str]:
    try:
        from PyPDF2 import PdfReader  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return ()

    try:
        reader = PdfReader(stream=pdf_bytes)
    except Exception:
        return ()

    pages: List[str] = []
    upto = min(len(reader.pages), max_pages)
    for idx in range(upto):
        try:
            text = reader.pages[idx].extract_text() or ""
        except Exception:
            text = ""
        pages.append(text)
    return tuple(pages)


def _extract_first_pages(pdf_bytes: bytes, max_pages: int) -> Sequence[str]:
    pages = _extract_pages_with_fitz(pdf_bytes, max_pages)
    if pages:
        return pages
    return _extract_pages_with_pypdf(pdf_bytes, max_pages)


def _normalise_keywords(keywords: Iterable[str]) -> List[str]:
    return [kw.strip().lower() for kw in keywords if kw and kw.strip()]


def validate_salgsoppgave_pdf(
    pdf_bytes: bytes,
    *,
    keywords: Iterable[str] | None = None,
    extra_match_terms: Iterable[str] | None = None,
    min_bytes: int = _MIN_BYTES,
    max_pages: int = _PAGE_SCAN_LIMIT,
) -> PdfValidationResult:
    """
    Validate that a PDF looks like a FINN salgsoppgave/prospekt.

    Conditions:
      * File size must be above ``min_bytes``.
      * The first ``max_pages`` pages must contain at least one of the supplied keywords.
        If ``extra_match_terms`` are provided, they also count towards a positive match.
    """
    size = len(pdf_bytes)
    if size < min_bytes:
        return PdfValidationResult(
            ok=False,
            reason=f"too_small:{size}",
            matched_keywords=[],
            pages_scanned=0,
            bytes_size=size,
        )

    scan_keywords = _normalise_keywords(keywords or _DEFAULT_KEYWORDS)
    extra_terms = _normalise_keywords(extra_match_terms or [])

    pages = _extract_first_pages(pdf_bytes, max_pages)
    if not pages:
        return PdfValidationResult(
            ok=False,
            reason="text_extraction_failed",
            matched_keywords=[],
            pages_scanned=0,
            bytes_size=size,
        )

    joined = "\n".join(page or "" for page in pages)
    lowered = joined.lower()
    matches: List[str] = []

    for term in scan_keywords + extra_terms:
        if term and term in lowered:
            matches.append(term)

    if not matches:
        return PdfValidationResult(
            ok=False,
            reason="missing_keywords",
            matched_keywords=[],
            pages_scanned=len(pages),
            bytes_size=size,
        )

    return PdfValidationResult(
        ok=True,
        reason=None,
        matched_keywords=matches,
        pages_scanned=len(pages),
        bytes_size=size,
    )


__all__ = ["PdfValidationResult", "validate_salgsoppgave_pdf"]
