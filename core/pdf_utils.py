# core/pdf_utils.py
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Optional,
    Tuple,
    Dict,
    List,
    Protocol,
    Any,
    Union,
    Sequence,
    TYPE_CHECKING,
    cast,
)

IMPL_VERSION = "prospekt_only_tr_locator_2025-09-24"

# ---- Primær-PDF verktøy (PyPDF2 for IO / fallback-tekst) ----
try:
    from PyPDF2 import PdfReader, PdfWriter  # pip install PyPDF2
except Exception:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment]
    PdfWriter = None  # type: ignore[assignment]

# ---- Bedre tekstekstraksjon (PyMuPDF/Fitz) (valgfritt) ----
try:
    import fitz  # type: ignore  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None  # type: ignore[assignment]

# ---- OCR fallback (valgfritt) ----
try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]

# Statiske typekontroller skal ikke kreve at valgfri deps er installert
if TYPE_CHECKING:  # noqa: SIM108
    import fitz as _fitz  # type: ignore
    import pytesseract as _pytesseract  # type: ignore
    from PIL import Image as _PILImage  # type: ignore


# --------------------------
# Heuristikker / mønstre
# --------------------------
CUE_PATTERNS = [
    r"\btilstandsrapport\b",
    r"\bboligsalgsrapport\b",
    r"\btilstandsrapport\s*\(ns\s*3600\)\b",
    r"\bns\s*3600\b",
    r"\bbygningsdeler\b",
    r"\bbyggteknisk?\s+gjennomgang\b",
    r"\bsammendrag av boligens tilstand\b",
    r"\boppsummering av avvik\b",
    r"\bkonklusjon\b",
    r"\btilstandsgrad\b",
    r"\btg\s*[0-3]\b",
    r"\bavvik\s+og\s+tiltak\b",
    r"\bnorsk\s*takst\b",
    r"\bnito\s*takst\b",
    r"\bbygningssakkyndig\b",
    r"\banticimex\b",
    r"\btaksthuset\b",
    r"\bnorconsult\b",
    r"\bbyggmester\b",
    r"\bnøkkeltakst\b",
    r"\btakstmann\b",
]

FOLLOW_CUE_PATTERNS = [
    r"\bvåtrom\b",
    r"\butvendig\b",
    r"\binnvendig\b",
    r"\belektrisk(?:e|)\s+anlegg\b|\bsikringsskap\b",
    r"\bavløpsrør\b|\bvanntilførsel\b|\bsluk\b",
    r"\bvinduer\b|\bdører\b|\btak\b|\bdrenering\b",
    r"\btilstandsgrad\b|\btg\s*[0-3]\b",
    r"\barealer\b|\bbra\b|\bns\s*3940\b",
]

SOFT_TERMINATOR_PATTERNS = [
    r"\bvedlegg\b",
    r"\bkilder\s+og\s+vedlegg\b",
    r"\bplaninformasjon\b",
    r"\btegninger\b",
    r"\bkart\b",
    r"\bopplysninger\s+fra\s+kommunen\b",
    r"\bkommunale\s+opplysninger\b",
    r"\bforretningsfører\b",
]

STRONG_TERMINATOR_PATTERNS = [
    r"\begenerkl(æring|aering)\b",
    r"\bselgers\s+egenerkl(æring|aering)\b",
    r"\bboligselgerforsikring\b",
    r"\bbud(skjem|reglement)\b|\bkjøpetilbud\b",
    r"\benergiattest\b",
    r"\bnabolagsprofil\b",
    r"\bmeglerpakke\b",
    r"\bforbrukerinformasjon\s+om\s+budgivning\b|\bbudgivning\b",
    r"\b(liste over )?løsøre\b",
    r"\b(bilder|foto)\b",
]

EXTRA_STOP_PATTERNS = [
    r"\barealer,\s*byggetegninger\s+og\s+brannceller\b",
    r"^\s*arealer\s*$",
    r"\bbefarings\s*-\s*og\s+eiendomsopplysninger\b",
    r"\bp-?rom\b",
    r"\bs-?rom\b",
    r"\bbruksareal\b",
]

# Aggressiv fallback (for prospekter uten tydelig TOC/anker – typisk Proaktiv/Webmegler)
AGGR_STRONG_START = [
    r"\btilstandsrapport\b",
    r"\bnøkkeltakst\b",
    r"\bbygningssakkyndig\b",
    r"\bbyggemåte\b",
    r"\bboligsalgsrapport\b",
]
AGGR_SOFT_START = [
    r"\bbefaring\b",
    r"\bbygningsdeler\b",
    r"\btilstandsgrad\b",
    r"\btg\s*[0-3]\b",
]
AGGR_HARD_STOP = [
    r"\begenerkl(æring|aering)\b",
    r"\bselgers\s+egenerkl(æring|aering)\b",
    r"\benergiattest\b",
    r"\bnabolagsprofil\b",
    r"\bforbrukerinformasjon\s+om\s+budgivning\b",
    r"\bbudgivning\b",
]
AGGR_MIN_PAGES = 4
AGGR_MAX_PAGES = 40

CUE_RX = re.compile("|".join(CUE_PATTERNS), re.I)
FOLLOW_CUE_RX = re.compile("|".join(FOLLOW_CUE_PATTERNS), re.I)
SOFT_TERM_RX = re.compile("|".join(SOFT_TERMINATOR_PATTERNS), re.I)
STRONG_TERM_RX = re.compile("|".join(STRONG_TERMINATOR_PATTERNS), re.I)
EXTRA_STOP_RX = re.compile("|".join(EXTRA_STOP_PATTERNS), re.I)

LOW_STREAK_STOP = 4  # sider uten TR-cues før vi antar slutt
MIN_BLOCK_SCORE = 8
OCR_SCALE = 2.0

# Praktisk alias for Path-strenger i typehint
StrPath = Union[str, Path]


# --------------------------
# Typer
# --------------------------
class _PdfPage(Protocol):
    def extract_text(self) -> str | None: ...


class _PdfLike(Protocol):
    pages: Sequence[_PdfPage] | Any  # Any for løse stubs


@dataclass
class PageText:
    text: str
    engine: str  # 'fitz', 'pypdf2', 'ocr' eller 'none'


# --------------------------
# I/O-helpere for bytes/Path
# --------------------------
def _read_bytes(source: Union[StrPath, bytes, bytearray]) -> bytes:
    """
    Sikker lesing av PDF-innhold som tilfredsstiller Pylance.
    Unngår å sende en Union til Path(...).
    """
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    # her er source statisk begrenset til StrPath
    spath: StrPath = cast(StrPath, source)
    return Path(spath).read_bytes()


# --------------------------
# Teksthjelpere
# --------------------------
def _extract_text_pymupdf(doc: Any, i: int) -> str:
    try:
        page = doc.load_page(i)
        txt = page.get_text("text") or ""
        return txt
    except Exception:
        return ""


def _extract_text_pypdf2(reader: _PdfLike, i: int) -> str:
    try:
        t = reader.pages[i].extract_text()  # type: ignore[index]
        return t or ""
    except Exception:
        return ""


def _extract_text_ocr(doc: Any, i: int) -> str:
    if pytesseract is None or Image is None or fitz is None:
        return ""
    try:
        page = doc.load_page(i)
        mat = fitz.Matrix(OCR_SCALE, OCR_SCALE)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        # Norsk først, eng fallback
        try:
            _ = pytesseract.get_languages(config="")
            lang = "nor+eng"
        except Exception:
            lang = "eng"
        txt = pytesseract.image_to_string(img, lang=lang)
        return txt or ""
    except Exception:
        return ""


def _get_page_texts_from_bytes(
    pdf_bytes: Union[bytes, bytearray], ocr: bool = True
) -> List[PageText]:
    texts: List[PageText] = []

    if fitz is not None:
        try:
            with fitz.open(stream=bytes(pdf_bytes), filetype="pdf") as doc:
                for i in range(doc.page_count):
                    t = _extract_text_pymupdf(doc, i)
                    texts.append(PageText(t or "", "fitz" if t else "none"))
                if ocr:
                    for i, pt in enumerate(texts):
                        if not pt.text.strip():
                            t_ocr = _extract_text_ocr(doc, i)
                            if t_ocr.strip():
                                texts[i] = PageText(t_ocr, "ocr")
                return texts
        except Exception:
            pass

    if PdfReader is not None:
        try:
            reader: _PdfLike = PdfReader(io.BytesIO(bytes(pdf_bytes)))  # type: ignore[call-arg]
            n = len(reader.pages)  # type: ignore[arg-type]
            for i in range(n):
                t = _extract_text_pypdf2(reader, i)
                texts.append(PageText(t or "", "pypdf2" if t else "none"))
            return texts
        except Exception:
            pass

    return []


def read_pdf_by_page(
    source: Union[str, Path, bytes, bytearray], *, ocr: bool = True
) -> List[PageText]:
    """
    Praktisk API for UI/LLM: les PDF fra path/bytes → liste av PageText.
    """
    data = _read_bytes(source)
    return _get_page_texts_from_bytes(data, ocr=ocr)


# --------------------------
# iVerdi/DNB-malsignal (hint)
# --------------------------
def _looks_like_iverdi_template_page(txt: str) -> bool:
    lo = (txt or "").lower()
    signals = 0
    if "oppdragsnr" in lo or "oppdragsnr." in lo:
        signals += 1
    if "befaringsdato" in lo or "rapportdato" in lo:
        signals += 1
    if "©" in txt and "verdi" in lo:
        signals += 1
    if " i verdi" in lo or "iverdi" in lo:
        signals += 1
    if "gå til side" in lo:
        signals += 1
    if "side:" in lo and "av" in lo:
        signals += 1
    return signals >= 2


# --------------------------
# Scoring (fallback)
# --------------------------
def _looks_like_vedlegg_index(txt: str) -> bool:
    lo = (txt or "").lower()
    if "vedlegg" not in lo:
        return False
    if "tilstandsrapport" in lo:
        return True
    if txt.count("\n") >= 5:
        return True
    return False


def _score_page(txt: str) -> int:
    if not txt:
        return -1
    lo = txt.lower()

    sc = 0
    if re.search(r"\btilstandsrapport\b|\bboligsalgsrapport\b|\bns\s*3600\b", lo):
        sc += 5
    if re.search(r"\btg\s*[0-3]\b|\btilstandsgrad\b", lo):
        sc += 4
    if re.search(r"\bbygningsdeler\b|\bbyggteknisk|\bbyggemåte\b", lo):
        sc += 3
    if re.search(r"\bsammendrag\b|\boppsummering\b|\bkonklusjon\b", lo):
        sc += 2
    if re.search(
        r"\bnorsk\s*takst\b|\bnito\s*takst\b|\banticimex\b|\btaksthuset\b|\bbyggmester\b|norconsult|takstmann|\bnøkkeltakst\b",
        lo,
    ):
        sc += 2

    cue = bool(CUE_RX.search(lo))
    soft_term = bool(SOFT_TERM_RX.search(lo))
    strong_term = bool(STRONG_TERM_RX.search(lo))
    extra_stop = bool(EXTRA_STOP_RX.search(lo))

    if strong_term and not cue:
        sc -= 20
    elif strong_term and cue:
        sc -= 2
    if soft_term and not cue:
        sc -= 5
    if extra_stop and not cue:
        sc -= 10
    if _looks_like_vedlegg_index(txt):
        sc -= 12
    return sc


def _find_best_block(page_scores: List[int]) -> Tuple[Optional[int], Optional[int]]:
    n = len(page_scores)
    best_sum = -(10**9)
    best_span: Tuple[Optional[int], Optional[int]] = (None, None)

    i = 0
    while i < n:
        while i < n and page_scores[i] < 2:
            i += 1
        if i >= n:
            break

        j = i
        running = 0
        low_streak = 0
        while j < n:
            s = page_scores[j]
            running += s
            if s <= 0:
                low_streak += 1
            else:
                low_streak = 0
            if low_streak >= LOW_STREAK_STOP:
                j -= low_streak
                break
            j += 1

        if j < i:
            j = i

        if running > best_sum and running >= MIN_BLOCK_SCORE:
            best_sum = running
            best_span = (i, j)

        i = max(j + 1, i + 1)

    return best_span


# --------------------------
# Flags
# --------------------------
def _flags_for_pages(texts: List[PageText]) -> List[Dict[str, bool]]:
    rows: List[Dict[str, bool]] = []
    for pt in texts:
        lo = (pt.text or "").lower()
        rows.append(
            {
                "cue": bool(CUE_RX.search(lo)),
                "follow_cue": bool(FOLLOW_CUE_RX.search(lo)),
                "soft_term": bool(SOFT_TERM_RX.search(lo)),
                "strong_term": bool(STRONG_TERM_RX.search(lo)),
                "extra_stop": bool(EXTRA_STOP_RX.search(lo)),
                "vedlegg_idx": _looks_like_vedlegg_index(pt.text or ""),
                "iverdi_tpl": _looks_like_iverdi_template_page(pt.text or ""),
                "is_contents_page": (
                    ("innhold" in lo and "vedlegg" in lo)
                    or ("vedlegg" in lo and "tilstandsrapport" in lo)
                ),
            }
        )
    return rows


# --------------------------
# Anker-metoder (primær)
# --------------------------
def _has_title_in_first_lines_or_head(
    text: str,
    term: str = "tilstandsrapport",
    max_lines: int = 20,
    head_chars: int = 1200,
) -> bool:
    if not text:
        return False
    lo = text.lower()
    lines = [l.strip() for l in lo.splitlines()[:max_lines] if l.strip()]
    if any(term in l for l in lines):
        return True
    return term in lo[:head_chars]


def _is_strict_tr_title(text: str, max_lines: int = 10) -> bool:
    """
    Streng tittel-sjekk: linje som starter med 'tilstandsrapport' eller 'boligsalgsrapport'
    i de første max_lines ikke-tomme linjene. Skipper 'innhold', 'informasjon', 'fakta', 'vedlegg'.
    """
    if not text:
        return False
    lines = [l.strip().lower() for l in (text.splitlines()[:max_lines] if text else [])]
    lines = [l for l in lines if l]
    bad_heads = ("innhold", "informasjon", "fakta", "vedlegg")
    for l in lines:
        if any(b in l for b in bad_heads):
            continue
        if l.startswith("tilstandsrapport") or l.startswith("boligsalgsrapport"):
            return True
    return False


def _parse_toc_candidates(text: str) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.search(r"(.*?)(?:\s+side)?\s+(\d{1,3})\s*$", line, flags=re.I)
        if not m:
            continue
        title = re.sub(r"\.+\s*$", "", m.group(1)).strip().lower()
        try:
            page_no = int(m.group(2))
        except Exception:
            continue
        out.append((title, page_no))
    return out


def _toc_find_tr_start(texts: List[PageText]) -> Optional[int]:
    n = len(texts)
    for i, pt in enumerate(texts[: min(n, 30)]):
        lo = (pt.text or "").lower()
        if ("innhold" in lo or "vedlegg" in lo) and len(lo) > 30:
            pairs = _parse_toc_candidates(pt.text or "")
            for title, page_no in pairs:
                if "tilstandsrapport" in title or "boligsalgsrapport" in title:
                    idx = max(0, page_no - 1)  # 1-basert → 0-basert
                    if idx < n:
                        for j in range(idx, min(n, idx + 4)):
                            if _has_title_in_first_lines_or_head(texts[j].text or ""):
                                return j
                        return idx
    return None


def _candidate_tr_starts(
    texts: List[PageText], flags: List[Dict[str, bool]]
) -> List[int]:
    n = len(texts)
    strict: List[int] = []
    weak: List[int] = []

    toc_idx = _toc_find_tr_start(texts)

    def allowed(i: int) -> bool:
        if (
            flags[i]["vedlegg_idx"]
            or flags[i]["is_contents_page"]
            or flags[i]["extra_stop"]
        ):
            return False
        if toc_idx is not None and i < toc_idx:
            return False
        return True

    # Strenge tittel-kandidater
    for i in range(n):
        if not allowed(i):
            continue
        if _is_strict_tr_title(texts[i].text or ""):
            strict.append(i)

    # Svake kandidater (title in head/first lines)
    for i in range(n):
        if not allowed(i):
            continue
        if _has_title_in_first_lines_or_head(texts[i].text or ""):
            if i not in strict:
                weak.append(i)

    seen = set()
    out: List[int] = []
    for i in strict if strict else weak:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _find_anchor_start(
    texts: List[PageText], flags: List[Dict[str, bool]]
) -> Optional[int]:
    cands = _candidate_tr_starts(texts, flags)
    if not cands:
        return None

    best_i, best_score = None, -(10**9)
    for i in cands:
        is_strict = _is_strict_tr_title(texts[i].text or "")
        ahead = 12
        n = len(texts)
        cnt = 0
        for j in range(i, min(n, i + ahead + 1)):
            if flags[j]["follow_cue"] or flags[j]["cue"]:
                cnt += 1
        bonus = 8 if is_strict else 0
        head = (texts[i].text or "").lower()[:400]
        if "innhold" in head or "informasjon" in head:
            bonus -= 3
        score = cnt * 2 + bonus
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def _find_anchor_end(
    start: int, texts: List[PageText], flags: List[Dict[str, bool]]
) -> int:
    n = len(texts)
    end = start
    low_streak = 0

    for j in range(start, n):
        end = j
        fl = flags[j]
        strong_stop = fl.get("strong_term") and not fl.get("cue")
        extra_stop = fl.get("extra_stop") and not fl.get("cue")
        if j > start and (strong_stop or extra_stop):
            end = j - 1
            break

        if not (fl["follow_cue"] or fl["cue"]):
            low_streak += 1
        else:
            low_streak = 0

        if low_streak >= LOW_STREAK_STOP:
            end = max(start, j - low_streak)
            break

    if end < start:
        end = start
    return end


# --------------------------
# Aggressiv fallback helpers
# --------------------------
def _aggr_norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _aggr_find_start_stop(pages_text: List[str]) -> Tuple[Optional[int], Optional[int]]:
    """Aggressiv nøkkelord-basert fallback for prospekter uten tydelig TOC/anker."""
    if not pages_text:
        return None, None

    # 1) Finn «best» start: side med flest sterke treff; bonus for «tilstandsrapport»
    best_i, best_score = None, -1
    for i, t in enumerate(pages_text):
        lo = _aggr_norm(t)
        strong = sum(bool(re.search(p, lo)) for p in AGGR_STRONG_START)
        soft = sum(bool(re.search(p, lo)) for p in AGGR_SOFT_START)
        if "tilstandsrapport" in lo:
            strong += 1  # liten bonus
        score = 10 * strong + 3 * soft
        if score > best_score:
            best_score, best_i = score, i

    if best_i is None or best_score < 6:  # terskel for å kalle det «start»
        return None, None

    # 2) Finn stopp etter start
    start = best_i
    n = len(pages_text)
    hard_stop_rx = re.compile("|".join(AGGR_HARD_STOP), re.I)
    for j in range(start + 1, n):
        if hard_stop_rx.search(_aggr_norm(pages_text[j])):
            end = max(start, j - 1)
            span_len = end - start + 1
            if AGGR_MIN_PAGES <= span_len <= AGGR_MAX_PAGES:
                return start, end
            break

    # 3) Hvis ingen hard stop → kutt på tak
    end = min(n - 1, start + AGGR_MAX_PAGES - 1)
    if (end - start + 1) >= AGGR_MIN_PAGES:
        return start, end
    return None, None


# --------------------------
# Walk/stop (for fallback-scoring)
# --------------------------
def _should_stop_at(
    j: int,
    start: int,
    texts: List[PageText],
    scores: List[int],
    flags: List[Dict[str, bool]],
    min_pages_before_stop: int = 6,
    lookahead: int = 3,
) -> bool:
    if j <= start:
        return False

    fl = flags[j]
    strong_stop = fl.get("strong_term") and not fl.get("cue")
    extra_stop = fl.get("extra_stop") and not fl.get("cue")

    if not (strong_stop or extra_stop):
        return False
    if (j - start + 1) < min_pages_before_stop:
        return False

    n = len(texts)
    ahead = 0
    tr_like = 0
    for k in range(1, lookahead + 1):
        idx = j + k
        if idx >= n:
            break
        ahead += 1
        if flags[idx]["cue"] or scores[idx] >= 2 or flags[idx].get("iverdi_tpl"):
            tr_like += 1

    if ahead == 0:
        return True
    if tr_like >= (ahead // 2 + 1):
        return False
    return True


def _walk_span_from(
    start: int, texts: List[PageText], scores: List[int], flags: List[Dict[str, bool]]
) -> Tuple[int, int]:
    n = len(texts)
    i = start
    low_streak = 0
    end = start

    while i < n:
        end = i
        if _should_stop_at(
            i, start, texts, scores, flags, min_pages_before_stop=6, lookahead=3
        ):
            end = max(start, i - 1)
            break

        if (scores[i] <= 0) and (not flags[i]["cue"]):
            low_streak += 1
        else:
            low_streak = 0

        if low_streak >= LOW_STREAK_STOP:
            end = max(start, i - low_streak)
            break

        i += 1

    while end + 1 < n:
        nxt = end + 1
        if flags[nxt]["extra_stop"]:
            break
        if (scores[nxt] > 0) or flags[nxt]["cue"] or flags[nxt].get("iverdi_tpl"):
            end = nxt
            continue
        break

    return start, min(end, n - 1)


# --------------------------
# API
# --------------------------
def detect_tilstandsrapport_span(
    pdf_bytes: Union[bytes, bytearray],
) -> Tuple[Optional[int], Optional[int], Dict]:
    """
    Finn side-intervallet (start, end) for TR i en *salgsoppgave/prospekt*.
    Returnerer (start_idx, end_idx, meta).
    """
    info: Dict[str, Any] = {
        "num_pages": None,
        "engine_hint": None,
        "scores": [],
        "flags": [],
        "method": "anchor",
        "impl_version": IMPL_VERSION,
        "details": {},
    }

    texts = _get_page_texts_from_bytes(pdf_bytes, ocr=True)
    n = len(texts)
    info["num_pages"] = n
    if n == 0:
        return None, None, {**info, "error": "no_pages_or_no_engine"}

    flags = _flags_for_pages(texts)
    scores = [_score_page(pt.text) for pt in texts]
    info["flags"] = flags
    info["scores"] = scores

    # 1) Anker først (TOC-gated + strict title-prioritet)
    start = _find_anchor_start(texts, flags)
    if start is not None:
        end = _find_anchor_end(start, texts, flags)
        info["method"] = "anchor"
        if (end - start + 1) >= 4:
            engines: Dict[str, int] = {}
            for pt in texts:
                engines[pt.engine] = engines.get(pt.engine, 0) + 1
            info["engine_hint"] = (
                max(engines.items(), key=lambda kv: kv[1])[0] if engines else None
            )
            return start, end, info
        else:
            info["details"]["anchor_too_short"] = (start, end)

    # 1.5) Aggressiv nøkkelord-fallback (Proaktiv/Webmegler m.fl.)
    aggr_start, aggr_end = _aggr_find_start_stop([pt.text for pt in texts])
    if aggr_start is not None and aggr_end is not None:
        info["method"] = "aggressive_keywords"
        info["impl_version"] = IMPL_VERSION
        engines: Dict[str, int] = {}
        for pt in texts:
            engines[pt.engine] = engines.get(pt.engine, 0) + 1
        info["engine_hint"] = (
            max(engines.items(), key=lambda kv: kv[1])[0] if engines else None
        )
        return aggr_start, aggr_end, info

    # 2) Fallback: scoring
    info["method"] = "fallback_block"
    s, e = _find_best_block(scores)
    if s is not None and e is not None:
        engines: Dict[str, int] = {}
        for pt in texts:
            engines[pt.engine] = engines.get(pt.engine, 0) + 1
        info["engine_hint"] = (
            max(engines.items(), key=lambda kv: kv[1])[0] if engines else None
        )
        return s, e, info

    return None, None, {**info, "error": "no_span_found"}


def extract_tilstandsrapport(input_path: str | Path, output_path: str | Path) -> bool:
    """
    Lokal hjelpefunksjon for *klipping* dersom du ønsker å lagre TR separat.
    Fetch-løypen din bruker ikke denne (prospekt-only), men vi beholder API-et.
    """
    if PdfReader is None or PdfWriter is None:  # pragma: no cover
        return False

    inp = Path(input_path)
    if not inp.exists():
        return False

    data = inp.read_bytes()
    start, end, _meta = detect_tilstandsrapport_span(data)
    if start is None or end is None:
        return False

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()

    end = min(end, len(reader.pages) - 1)
    for i in range(start, end + 1):
        try:
            writer.add_page(reader.pages[i])
        except Exception:
            continue

    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("wb") as f:
        writer.write(f)

    return True


# ---- Nytt: Lettvekts-API for LLM --------------------------------------------
def extract_tr_text(
    pdf_source: Union[str, Path, bytes, bytearray], *, ocr: bool = True
) -> Dict[str, Any]:
    """
    Praktisk funksjon for UI/LLM:
      - Leser hele prospektet (path eller bytes)
      - Finner TR-span
      - Returnerer side-tekster + sammenslått tekst for TR
    Return:
      {
        "start": int|None,
        "end": int|None,
        "pages_text": [PageText, ...],   # for hele prospektet (kan caches)
        "text": "…",                     # kun TR, eller "" hvis ikke funnet
        "meta": {...}                    # detect_tilstandsrapport_span meta
      }
    """
    data = _read_bytes(pdf_source)
    pages = _get_page_texts_from_bytes(data, ocr=ocr)
    s, e, meta = detect_tilstandsrapport_span(data)
    if s is None or e is None:
        return {
            "start": None,
            "end": None,
            "pages_text": pages,
            "text": "",
            "meta": meta,
        }
    text = "\n\n".join((pages[i].text or "") for i in range(s, e + 1))
    return {"start": s, "end": e, "pages_text": pages, "text": text, "meta": meta}


# === Diagnose ================================================================
from collections import Counter


def diagnose_tilstandsrapport(
    pdf_path: str | Path, *, ocr: bool = True, preview_chars: int = 110
) -> dict:
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(f"Fant ikke PDF: {p}")

    data = p.read_bytes()
    texts = _get_page_texts_from_bytes(data, ocr=ocr)
    n = len(texts)
    if n == 0:
        print("Ingen sider/ingen tekstmotor tilgjengelig.")
        return {"num_pages": 0, "rows": []}

    scores = [_score_page(pt.text) for pt in texts]
    flags = _flags_for_pages(texts)
    start, end, meta = detect_tilstandsrapport_span(data)

    engine_counts = Counter([pt.engine for pt in texts])
    info = {
        "num_pages": n,
        "engine_counts": dict(engine_counts),
        "scores": scores,
        "flags": flags,
        "proposed_span": (start, end),
        "meta": meta,
    }

    print(f"\nPDF: {p} ({n} sider)")
    print("idx  len   eng   score  cue soft strong extra  preview")
    for i, pt in enumerate(texts):
        preview = " ".join((pt.text or "").split())[:preview_chars]
        fl = flags[i]
        print(
            f"{i:>3}  {len(pt.text or ''):>4} {pt.engine:<5}  {scores[i]:>5}   "
            f"{int(fl['cue']):>1}    {int(fl.get('soft_term', False)):>1}     {int(fl.get('strong_term', False)):>1}     {int(fl.get('extra_stop', False)):>1}  {preview}"
        )

    if start is not None and end is not None:
        print(
            f"\nFORESLÅTT SPAN: {start}..{end} → Preview: {start+1}..{end+1} "
            f"(method={meta.get('method') if isinstance(meta, dict) else None}, impl={meta.get('impl_version') if isinstance(meta, dict) else None})"
        )
    else:
        print("\nFORESLÅTT SPAN: None (klarte ikke å finne TR-blokk)")

    return info
