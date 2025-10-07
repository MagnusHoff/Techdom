from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from techdom.processing.pdf_utils import read_pdf_by_page

TG_PATTERN = re.compile(r"\btg\s*([23])\b", re.IGNORECASE)

STANDARD_COMPONENTS = [
    "Bad",
    "Våtromsmembran",
    "Drenering",
    "Tak",
    "Vinduer",
    "Yttervegger",
    "Ventilasjon",
    "Elektro/Sikringsskap",
    "Bereder",
    "Radon",
    "Pipe/ildsted",
]

COMPONENT_PREFIXES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Bad", ("bad", "bader", "wc", "dusj")),
    ("Våtromsmembran", ("vatrom", "våtrom", "membran", "sluk", "tettsjikt", "smoremembran")),
    ("Drenering", ("drener", "grunnmur", "fuktsik", "drens")),
    ("Tak", ("taktek", "takren", "takkon", "takste", "undertak", "yttertak", "saltak", "pulttak", "tak", "taket")),
    ("Vinduer", ("vindu", "vindus", "vindauge")),
    ("Yttervegger", ("yttervegg", "fasad", "kledning", "ytterkled")),
    ("Ventilasjon", ("ventil", "avtrekk", "lufting", "inneklima")),
    ("Elektro/Sikringsskap", ("elektr", "sikring", "elanlegg", "jordfeil", "sikringsskap", "kursfort")),
    ("Bereder", ("bereder", "varmtvann", "vvb")),
    ("Radon", ("radon",)),
    ("Pipe/ildsted", ("pipe", "ildsted", "skorstein", "pipelop", "pipeløp", "peis", "skorste")),
]

PUNCT_RX = re.compile(r"[.;,]+$")
WHITESPACE_RX = re.compile(r"\s+")


class ExtractionError(RuntimeError):
    """Raised when extraction fails for user-facing reasons."""


@dataclass
class Segment:
    text: str
    kilde_side: str


@dataclass
class FindingCandidate:
    component: str
    reason: str
    level: int
    kilde_side: str


def extract_tg(
    source_salgsoppgave: str,
    source_finn: str | None = None,
) -> dict[str, object]:
    segments: List[Segment] = []
    segments.extend(_segments_from_source(source_salgsoppgave, label="salgsoppgave"))
    if source_finn:
        segments.extend(_segments_from_source(source_finn, label="FINN"))

    candidates: List[FindingCandidate] = []
    for seg in segments:
        candidates.extend(_candidates_from_segment(seg))

    findings = _dedupe_candidates(candidates)

    tg3_entries = [
        {"komponent": f.component, "grunn": f.reason, "kilde_side": f.kilde_side}
        for f in findings
        if f.level == 3
    ]
    tg2_entries = [
        {"komponent": f.component, "grunn": f.reason, "kilde_side": f.kilde_side}
        for f in findings
        if f.level == 2
    ]

    tg3_entries = _limit_entries(tg3_entries)
    tg2_entries = _limit_entries(tg2_entries)

    markdown = _build_markdown(tg3_entries, tg2_entries)
    missing = [
        component
        for component in STANDARD_COMPONENTS
        if component not in {entry["komponent"] for entry in tg3_entries + tg2_entries}
    ]

    return {
        "markdown": markdown,
        "json": {"TG3": tg3_entries, "TG2": tg2_entries, "missing": missing},
    }


def _segments_from_source(source: str, *, label: str) -> List[Segment]:
    if _is_http_url(source):
        content, ctype = _download(source)
        if _looks_like_pdf(ctype, content, source):
            return _segments_from_pdf_bytes(content)
        return _segments_from_html(content.decode("utf-8", errors="ignore"), label)

    path = Path(source)
    if not path.exists():
        raise ExtractionError(f"fant ikke fil: {source}")

    suffix = path.suffix.lower()
    data = path.read_bytes()
    if suffix == ".pdf" or _looks_like_pdf(None, data, source):
        return _segments_from_pdf_bytes(data)
    return _segments_from_html(data.decode("utf-8", errors="ignore"), label)


def _limit_entries(entries: List[dict[str, str]], max_items: int = 10) -> List[dict[str, str]]:
    sorted_entries = sorted(
        entries,
        key=lambda item: (
            _component_order(item["komponent"]),
            item.get("grunn", ""),
        ),
    )
    return sorted_entries[:max_items]


def _component_order(component: str) -> int:
    try:
        return STANDARD_COMPONENTS.index(component)
    except ValueError:
        return len(STANDARD_COMPONENTS) + 1


def _build_markdown(
    tg3_entries: List[dict[str, str]], tg2_entries: List[dict[str, str]]
) -> str:
    lines: List[str] = []
    lines.append("TG3 (alvorlig):")
    for entry in tg3_entries:
        lines.append(f"- {entry['komponent']} – {entry['grunn']}")

    lines.append("")
    lines.append("TG2 (middels):")
    for entry in tg2_entries:
        lines.append(f"- {entry['komponent']} – {entry['grunn']}")

    return "\n".join(lines).strip()


def _dedupe_candidates(candidates: Iterable[FindingCandidate]) -> List[FindingCandidate]:
    best: dict[str, FindingCandidate] = {}
    for cand in candidates:
        current = best.get(cand.component)
        if current is None or cand.level > current.level:
            best[cand.component] = cand
        elif cand.level == current.level and len(cand.reason) < len(current.reason):
            best[cand.component] = cand
    return list(best.values())


def _candidates_from_segment(segment: Segment) -> List[FindingCandidate]:
    text = segment.text.strip()
    if not text:
        return []

    match = TG_PATTERN.search(text)
    if not match:
        return []

    component_match = _find_component(text)
    if component_match is None:
        return []

    level = int(match.group(1))
    reason = _extract_reason(text, match, component_match).strip()
    reason = _normalize_reason(reason)
    if not reason:
        return []

    return [
        FindingCandidate(
            component=component_match.component,
            reason=reason,
            level=level,
            kilde_side=segment.kilde_side,
        )
    ]


@dataclass
class ComponentMatch:
    component: str
    token: str
    start: int


def _find_component(text: str) -> ComponentMatch | None:
    ascii_text = _strip_diacritics(text.lower())
    best_match: ComponentMatch | None = None

    for token_match in re.finditer(r"[a-z0-9]+", ascii_text):
        token = token_match.group(0)
        start = token_match.start()
        for component, prefixes in COMPONENT_PREFIXES:
            if any(_token_matches_prefix(token, prefix) for prefix in prefixes):
                if best_match is None or start < best_match.start:
                    best_match = ComponentMatch(
                        component=component,
                        token=text[start : start + len(token)],
                        start=start,
                    )
    return best_match


def _extract_reason(text: str, match: re.Match[str], comp: ComponentMatch) -> str:
    def strip_component(value: str) -> str:
        cleaned = value
        if comp.token:
            cleaned = re.sub(
                re.escape(comp.token), " ", cleaned, flags=re.IGNORECASE, count=1
            )
        return cleaned.lstrip(" :-–•\t")

    after = text[match.end() :].strip(" :-–•\t")
    before = text[: match.start()].strip(" :-–•\t")

    candidates = [after, before]
    for candidate in candidates:
        candidate = strip_component(candidate)
        candidate = candidate.strip()
        if candidate:
            return candidate

    without = strip_component(text[: match.start()] + " " + text[match.end() :])
    return without.strip()


def _normalize_reason(reason: str) -> str:
    reason = reason.lower()
    reason = PUNCT_RX.sub("", reason)
    reason = WHITESPACE_RX.sub(" ", reason).strip()
    if not reason:
        return ""

    words = reason.split()
    if len(words) > 6:
        words = words[:6]
    if len(words) == 1:
        words.append("registrert")
    if not words:
        return ""
    return " ".join(words)


def _token_matches_prefix(token: str, prefix: str) -> bool:
    if prefix == "tak":
        if token.startswith("takst"):
            return False
        return token == "tak"
    if prefix == "taket":
        return token.startswith("taket")
    normalized_prefix = _strip_diacritics(prefix).replace("-", "")
    return token.startswith(normalized_prefix)


def _segments_from_pdf_bytes(data: bytes) -> List[Segment]:
    segments: List[Segment] = []
    pages = read_pdf_by_page(data, ocr=True)
    for idx, page in enumerate(pages, start=1):
        if not page.text:
            continue
        for line in page.text.splitlines():
            line = line.strip()
            if line:
                segments.append(Segment(text=line, kilde_side=str(idx)))
    return segments


def _segments_from_html(html: str, label: str) -> List[Segment]:
    segments: List[Segment] = []
    soup = BeautifulSoup(html or "", "html.parser")

    for tr in soup.select("table tr"):
        cells = [
            cell.get_text(" ", strip=True)
            for cell in tr.find_all(["td", "th"])
        ]
        row_text = WHITESPACE_RX.sub(" ", " ".join(cells)).strip()
        if row_text:
            segments.append(Segment(text=row_text, kilde_side=label))

    for tag in soup.find_all(["p", "li", "dd", "dt", "div", "span"]):
        if tag.find_parent("table"):
            continue
        text = tag.get_text(" ", strip=True)
        text = WHITESPACE_RX.sub(" ", text).strip()
        if text:
            segments.append(Segment(text=text, kilde_side=label))

    uniq: dict[tuple[str, str], Segment] = {}
    for seg in segments:
        key = (seg.text.lower(), seg.kilde_side.lower())
        if key not in uniq:
            uniq[key] = seg
    return list(uniq.values())


def _download(url: str) -> Tuple[bytes, str | None]:
    try:
        response = requests.get(url, timeout=30, allow_redirects=True)
    except requests.RequestException as exc:
        raise ExtractionError(f"lastet ikke ned {url}: {exc}") from exc

    if response.status_code in {401, 402, 403, 407}:
        raise ExtractionError("beskyttet – bruk lokal fil")
    if response.status_code >= 400:
        raise ExtractionError(f"http-feil {response.status_code} for {url}")

    return response.content, response.headers.get("Content-Type")


def _looks_like_pdf(
    ctype: str | None, data: bytes, source: str
) -> bool:
    if ctype and "pdf" in ctype.lower():
        return True
    if source.lower().endswith(".pdf"):
        return True
    return data[:4] == b"%PDF"


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"}
    except Exception:
        return False


def _strip_diacritics(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def to_json(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
