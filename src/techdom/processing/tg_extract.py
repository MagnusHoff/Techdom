from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from techdom.processing.pdf_utils import read_pdf_by_page

TG_PATTERN = re.compile(r"(?<!\w)TG\s*[:\-]?\s*([23])\b", re.IGNORECASE)

TG_LEVEL_TRIGGERS: Tuple[Tuple[re.Pattern[str], int, str], ...] = (
    (re.compile(r"(?<!\w)TG\s*[:\-]?\s*(3)\b", re.IGNORECASE), 3, "tg"),
    (re.compile(r"(?<!\w)TG\s*[:\-]?\s*(2)\b", re.IGNORECASE), 2, "tg"),
    (re.compile(r"Store eller alvorlige avvik", re.IGNORECASE), 3, "header"),
    (re.compile(r"Avvik som kan kreve tiltak", re.IGNORECASE), 2, "header"),
)

SUMMARY_TRIGGER = re.compile(r"Sammendrag av boligens tilstand", re.IGNORECASE)

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
    ("Trapp", ("trapp", "handlop", "handloper", "håndløp", "håndløper")),
    ("Pipe/ildsted", ("pipe", "ildsted", "skorstein", "pipelop", "pipeløp", "peis", "skorste")),
]

WHITESPACE_RX = re.compile(r"\s+")

IMPORTANT_COMPONENT_TOKENS = (
    "bad",
    "våtrom",
    "vatrom",
    "membran",
    "drener",
    "taknedløp",
    "grunnmur",
    "taknedløp",
    "kjeller",
    "tak",
    "taktek",
    "undertak",
    "elektro",
    "sikring",
    "jordfeil",
    "el-anlegg",
    "elanlegg",
    "varmtvann",
    "bereder",
    "pipe",
    "ildsted",
    "skorstein",
    "radon",
    "ventilasjon",
    "avløp",
    "avlop",
    "rør",
    "ror",
    "brann",
    "sprinkler",
    "sluk",
)

IMPORTANT_REASON_KEYWORDS = (
    "fukt",
    "fuktskade",
    "fuktmerke",
    "lekk",
    "lekkasje",
    "vann",
    "drener",
    "taknedløp",
    "grunnmur",
    "kjeller",
    "membran",
    "sluk",
    "fall mot sluk",
    "fall til sluk",
    "bakfall",
    "avløp",
    "avlop",
    "rør",
    "ror",
    "elektr",
    "sikring",
    "jordfeil",
    "brann",
    "ildsted",
    "pipe",
    "skorstein",
    "radon",
    "ventilasjon",
    "råte",
    "raate",
    "mugg",
    "sopp",
    "kondens",
    "korrosjon",
    "rust",
    "setning",
    "bærekonstruksjon",
    "konstruksjon",
    "utett",
    "teknisk rom",
    "varmtvann",
    "mangler",
    "handløper",
    "håndløper",
    "bom",
)

COSMETIC_SKIP_KEYWORDS = (
    "riper",
    "små hull",
    "hakk",
    "merker",
    "overflate",
    "maling",
    "sparkel",
    "tapet",
    "kosmetisk",
    "små skader",
    "løse fliser",
    "flateavvik",
    "overflateslitasje",
)

NEGATIVE_SKIP_PHRASES = (
    "ingen avvik",
    "ingen forhold",
    "ingen registrert",
    "ingen synlige skader",
    "normal slitasje",
)

LABEL_STOPWORDS = {
    "er",
    "som",
    "den",
    "det",
    "de",
    "til",
    "for",
    "med",
    "mot",
    "fra",
    "over",
    "under",
    "mellom",
    "innen",
    "innenfor",
    "utenfor",
    "og",
    "men",
    "eller",
    "at",
    "på",
    "i",
    "å",
    "har",
    "skal",
    "kan",
    "må",
    "bør",
    "blir",
    "ble",
    "blitt",
    "enn",
    "en",
    "et",
    "av",
    "ved",
    "mer",
    "halvparten",
    "side",
    "tg2",
    "tg3",
}

LABEL_OVERRIDES: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"taknedløp[^.]*over bakken", re.IGNORECASE), "Åpne taknedløp"),
    (re.compile(r"mangler\s+håndløper", re.IGNORECASE), "Manglende håndløper"),
    (re.compile(r"fall[^.]{0,80}sluk", re.IGNORECASE), "Lavt slukfall"),
    (re.compile(r"\bbom\b[^.]{0,80}flis", re.IGNORECASE), "Løs gulvflis"),
    (re.compile(r"råte", re.IGNORECASE), "Råteskade"),
    (re.compile(r"mugg", re.IGNORECASE), "Muggfare"),
    (re.compile(r"ikke godkjent", re.IGNORECASE), "Ikke godkjent"),
    (re.compile(r"lekk", re.IGNORECASE), "Lekkasjerisiko"),
)

LABEL_SIGNAL_TOKENS = {
    "fukt",
    "lekk",
    "lekkasje",
    "lekkasjer",
    "rate",
    "mugg",
    "mangler",
    "skade",
    "skader",
    "avvik",
    "sluk",
    "fall",
    "bom",
    "rust",
    "korrosjon",
    "svikt",
    "ikke",
    "godkjent",
    "brann",
    "drenering",
    "grunnmur",
    "kjeller",
    "tak",
}

PRIORITY_REASON_TOKENS = (
    "mangler",
    "fukt",
    "lekk",
    "lekkasje",
    "råte",
    "mugg",
    "bom",
    "fall",
    "ikke godkjent",
    "utett",
    "taknedløp",
)


class ExtractionError(RuntimeError):
    """Raised when extraction fails for user-facing reasons."""


@dataclass
class Segment:
    text: str
    kilde_side: str
    original_text: str | None = None

    def __post_init__(self) -> None:
        if self.original_text is None:
            self.original_text = self.text


@dataclass
class FindingCandidate:
    component: str
    reason: str
    level: int
    kilde_side: str


def _contains_any(text: str, keywords: Tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(keyword in lowered for keyword in keywords)


def _detect_level(text: str) -> tuple[int | None, str | None]:
    if not text:
        return None, None
    for pattern, level, kind in TG_LEVEL_TRIGGERS:
        if pattern.search(text):
            return level, kind
    if SUMMARY_TRIGGER.search(text):
        return None, "summary"
    return None, None


def _clean_reason_text(value: str) -> str:
    if not value:
        return ""
    text = WHITESPACE_RX.sub(" ", value).strip()
    text = re.sub(r"^[\-\*\u2022\u2043\u2219\u25cf]+\s*", "", text)
    text = re.sub(
        r"^(?:Forhold som har fått|Bygningsdeler med)\s+[^:]{0,150}?Oppsummering\s*[:\-]*\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^Oppsummering(?: av)?\s+[^:]{0,120}?[:\-]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    split = re.split(r"\b(Tiltak|Konsekvens|Arsak|Anbefaling)\b", text, flags=re.IGNORECASE)
    if split:
        text = split[0].strip()
    text = text.strip(" .,:;–—-")
    if not text:
        return ""
    sentences = [
        segment.strip(" .,:;–—-")
        for segment in re.split(r"[.!?]+", text)
        if segment.strip(" .,:;–—-")
    ]
    if sentences:
        text = sentences[0]
    text = text.strip(" .,:;–—-")
    if not text:
        return ""
    if text[0].isalpha():
        text = text[0].upper() + text[1:]
    if len(text) > 220:
        text = text[:217].rstrip(",;:- ") + "..."
    lowered = text.casefold()
    if lowered.startswith("oppsummering"):
        return ""
    if lowered.startswith("type "):
        return ""
    for skip_phrase in ("vedlagt salgsoppgaven", "i tillegg kan det gis", "fremlagt dokumentasjon"):
        if skip_phrase in lowered:
            return ""
    if not text.endswith("."):
        text = f"{text}."
    return text


def _is_relevant_reason(
    cleaned: str,
    *,
    component: str,
    original: str | None = None,
) -> bool:
    text = (original or cleaned).casefold()
    if not text:
        return False
    component_lower = (component or "").casefold()

    component_hit = any(token in component_lower for token in IMPORTANT_COMPONENT_TOKENS)
    keyword_hit = _contains_any(text, IMPORTANT_REASON_KEYWORDS)

    if not component_hit and not keyword_hit:
        return False

    if not keyword_hit and _contains_any(text, COSMETIC_SKIP_KEYWORDS):
        return False

    if not keyword_hit and _contains_any(text, NEGATIVE_SKIP_PHRASES):
        return False

    return True


def _build_result_from_segments(segments: Sequence[Segment]) -> dict[str, object]:
    segment_list = list(segments)
    candidates = _candidates_from_segments(segment_list)
    findings = _dedupe_candidates(candidates)
    findings = _collapse_component_duplicates(findings)

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

    markdown = _build_markdown(tg3_entries, tg2_entries)
    missing = [
        component
        for component in STANDARD_COMPONENTS
        if component
        not in {entry["komponent"] for entry in tg3_entries + tg2_entries}
    ]

    return {
        "markdown": markdown,
        "json": {"TG3": tg3_entries, "TG2": tg2_entries, "missing": missing},
    }


def extract_tg(
    source_salgsoppgave: str,
    source_finn: str | None = None,
) -> dict[str, object]:
    segments: List[Segment] = []
    segments.extend(_segments_from_source(source_salgsoppgave, label="salgsoppgave"))
    if source_finn:
        segments.extend(_segments_from_source(source_finn, label="FINN"))
    return _build_result_from_segments(segments)


def extract_tg_from_pdf_bytes(data: bytes) -> dict[str, object]:
    segments = _segments_from_pdf_bytes(data)
    return _build_result_from_segments(segments)


def format_tg_entries(
    entries: Iterable[Mapping[str, Any]],
    *,
    level: int,
    include_component: bool = True,
    include_source: bool = True,
    limit: int | None = 8,
) -> List[str]:
    """Format TG-funn til korte bulletpunkter."""

    formatted: List[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        component = str(entry.get("komponent") or entry.get("component") or "").strip()
        reason = str(entry.get("grunn") or entry.get("reason") or "").strip()
        source = str(entry.get("kilde_side") or entry.get("kilde") or "").strip()

        if not component and not reason:
            continue

        prefix = f"TG{level}"
        text: str
        if include_component and component and reason:
            text = f"{prefix} {component}: {reason}"
        elif include_component and component and not reason:
            text = f"{prefix} {component}"
        elif include_component and reason:
            text = f"{prefix} {reason}"
        else:
            text = reason or component

        text = text.strip()
        if not text:
            continue

        if include_source and source:
            label = _normalise_source_label(source)
            if label:
                text = f"{text} ({label})"

        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        formatted.append(text)
        if limit is not None and len(formatted) >= limit:
            break

    return formatted


def summarize_tg_entries(
    entries: Iterable[Mapping[str, Any]],
    *,
    level: int,
    include_source: bool = True,
    limit: int | None = 8,
) -> List[dict[str, str]]:
    """Lag korte etiketter + detaljer for TG-punkter."""

    summaries: List[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        component = str(entry.get("komponent") or entry.get("component") or "").strip()
        reason = str(entry.get("grunn") or entry.get("reason") or "").strip()
        source = str(entry.get("kilde_side") or entry.get("kilde") or "").strip()

        if not component and not reason:
            continue

        label = _build_summary_label(component, reason, level)
        detail = _build_summary_detail(component, reason, level, source if include_source else "")
        key = (label.casefold(), detail.casefold())
        if key in seen:
            continue
        seen.add(key)
        summaries.append(
            {
                "label": label,
                "detail": detail,
                "source": _normalise_source_label(source) if include_source else "",
            }
        )
        if limit is not None and len(summaries) >= limit:
            break
    return summaries


def summarize_tg_strings(strings: Iterable[str], *, level: int, limit: int | None = 8) -> List[dict[str, str]]:
    """Fallback for fritekstliste (AI-resultater)."""

    entries: List[dict[str, str]] = []
    for raw in strings:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        component, reason = _split_component_and_reason(text)
        entries.append({"komponent": component, "grunn": reason})
    return summarize_tg_entries(entries, level=level, include_source=False, limit=limit)


def _derive_short_reason(component: str, reason: str) -> str:
    text = reason.strip()
    if text:
        text = WHITESPACE_RX.sub(" ", text)
        text = text.rstrip(".").strip()
    if text:
        return text
    return component.strip()


def _compose_hover(level: int, component: str, reason: str, source: str) -> str:
    return _build_summary_detail(component, reason, level, source)


def build_v2_details(
    entries: Iterable[Mapping[str, Any]],
    *,
    level: int,
) -> List[dict[str, Any]]:
    details: List[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        component = str(entry.get("komponent") or entry.get("component") or "").strip()
        reason = str(entry.get("grunn") or entry.get("reason") or "").strip()
        source = str(entry.get("kilde_side") or entry.get("kilde") or "").strip()
        if not component and not reason:
            continue
        label = _build_summary_label(component, reason, level)
        short = _derive_short_reason(component, reason) or label
        hover = _compose_hover(level, component, reason, source)
        fingerprint = (label.casefold(), short.casefold(), level)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        details.append(
            {
                "label": label,
                "short": short,
                "hover": hover,
                "tg": level,
            }
        )
    return details


def build_v2_details_from_strings(
    strings: Iterable[str],
    *,
    level: int,
) -> List[dict[str, Any]]:
    formatted_entries: List[dict[str, str]] = []
    for raw in strings:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        component, reason = _split_component_and_reason(text)
        formatted_entries.append({"komponent": component, "grunn": reason})
    return build_v2_details(formatted_entries, level=level)


def _build_summary_label(component: str, reason: str, level: int) -> str:
    lowered_reason = reason.casefold()
    for pattern, label in LABEL_OVERRIDES:
        if pattern.search(lowered_reason):
            return label

    component_tokens = _extract_label_tokens(component)
    component_lookup = {_strip_diacritics(token.casefold()) for token in component_tokens}
    reason_tokens = _extract_label_tokens(reason, exclude=component_lookup)

    ordered_tokens: List[str] = []
    seen: set[str] = set()

    def add_token(token: str) -> None:
        lowered = token.casefold()
        if lowered in seen:
            return
        seen.add(lowered)
        ordered_tokens.append(token)

    def token_is_signal(token: str) -> bool:
        key = _strip_diacritics(token.casefold())
        return key in LABEL_SIGNAL_TOKENS

    reason_has_signal = any(token_is_signal(token) for token in reason_tokens)

    if reason_has_signal:
        for token in reason_tokens:
            add_token(token)
        for token in component_tokens:
            add_token(token)
    else:
        for token in component_tokens:
            add_token(token)
        for token in reason_tokens:
            add_token(token)

    if component_tokens:
        primary_component = component_tokens[0]
        comp_key = _strip_diacritics(primary_component.casefold())
        has_component = any(
            _strip_diacritics(token.casefold()) == comp_key for token in ordered_tokens[:3]
        )
        if not has_component:
            if len(ordered_tokens) >= 3:
                ordered_tokens[2] = primary_component
            else:
                ordered_tokens.append(primary_component)

    if not ordered_tokens:
        ordered_tokens = [f"TG{level}"]

    label = " ".join(ordered_tokens[:3]).strip()
    if not label:
        label = f"TG{level}"
    elif label[0].islower():
        label = label[0].upper() + label[1:]
    return label


def _build_summary_detail(component: str, reason: str, level: int, source: str) -> str:
    detail = reason.strip()
    component_clean = component.strip()
    if component_clean and component_clean.lower() not in detail.lower():
        if detail:
            detail = f"{component_clean}: {detail}"
        else:
            detail = component_clean
    detail = detail or component_clean or ""
    detail = detail.strip()
    if detail and not detail.endswith((".", "!", "?")):
        detail = f"{detail}."
    if detail:
        detail = f"TG{level} {detail}"
    else:
        detail = f"TG{level}"

    source_label = _normalise_source_label(source)
    if source_label:
        detail = f"{detail} ({source_label})"
    return detail


def _extract_label_tokens(value: str, exclude: set[str] | None = None) -> List[str]:
    if not value:
        return []
    tokens: List[str] = []
    exclude_lookup = {_strip_diacritics(token.lower()) for token in (exclude or set())}
    for match in re.finditer(r"[A-Za-zÆØÅæøå0-9][A-Za-zÆØÅæøå0-9\-]*", value):
        token = match.group(0).strip("-")
        if not token:
            continue
        lowered = _strip_diacritics(token.lower())
        if lowered in LABEL_STOPWORDS or lowered in exclude_lookup:
            continue
        if lowered.startswith("tg"):
            continue
        if len(lowered) <= 2 and lowered not in {"bom"}:
            continue
        tokens.append(token)
    return tokens


def _split_component_and_reason(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    text = value.strip()
    text = re.sub(r"^\s*TG\s*[-:]?\s*(\d)\s*", "", text, flags=re.IGNORECASE)
    match = re.split(r"\s*[:\-–]\s*", text, maxsplit=1)
    if len(match) == 2:
        component, reason = match
    else:
        component, reason = "", text
    return component.strip(), reason.strip()


def _normalise_source_label(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    lowered = cleaned.casefold()
    if cleaned.isdigit():
        return f"Tilstandsrapport side {cleaned}"
    if lowered.startswith("side"):
        number_match = re.search(r"\d+", cleaned)
        if number_match:
            return f"Tilstandsrapport side {number_match.group(0)}"
        return "Tilstandsrapport"
    if lowered in {"salgsoppgave", "finn"}:
        return cleaned.capitalize()
    return cleaned


def merge_tg_lists(
    primary: Sequence[str],
    secondary: Sequence[str],
    *,
    limit: int | None = 8,
) -> List[str]:
    """Slå sammen to lister med TG-punkter uten duplikater."""
    merged: List[str] = []
    seen: set[str] = set()
    for source in (primary, secondary):
        for item in source:
            if not item:
                continue
            text = str(item).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
            if limit is not None and len(merged) >= limit:
                return merged
    return merged


def coerce_tg_strings(value: Any) -> List[str]:
    """Koerser innkommende TG-lister til en renskåret liste med strenger."""
    if value is None:
        return []
    if isinstance(value, str):
        iter_value: Iterable[Any] = [value]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        iter_value = value
    else:
        return []

    result: List[str] = []
    for item in iter_value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


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


def _build_markdown(
    tg3_entries: List[dict[str, str]], tg2_entries: List[dict[str, str]]
) -> str:
    lines: List[str] = []
    lines.append("TG2")
    if tg2_entries:
        lines.extend(entry["grunn"] for entry in tg2_entries)
    else:
        lines.append("Ingen TG2-punkter funnet.")

    lines.append("")
    lines.append("TG3")
    if tg3_entries:
        lines.extend(entry["grunn"] for entry in tg3_entries)
    else:
        lines.append("Ingen TG3-punkter funnet.")

    return "\n".join(lines).strip()


def _dedupe_candidates(candidates: Iterable[FindingCandidate]) -> List[FindingCandidate]:
    deduped: List[FindingCandidate] = []
    index_map: dict[tuple[str, str], int] = {}
    for cand in candidates:
        component_key = cand.component.casefold()
        reason_key = WHITESPACE_RX.sub(" ", cand.reason.casefold()).strip()
        key = (component_key, reason_key)
        existing_index = index_map.get(key)
        if existing_index is None:
            index_map[key] = len(deduped)
            deduped.append(cand)
            continue

        existing = deduped[existing_index]
        if cand.level > existing.level:
            deduped[existing_index] = cand
        elif cand.level == existing.level and len(cand.reason) > len(existing.reason):
            deduped[existing_index] = cand
    return deduped


def _score_reason_value(reason: str) -> int:
    lowered = reason.casefold()
    score = 0
    if lowered.startswith("er "):
        score -= 2
    if _contains_any(lowered, IMPORTANT_REASON_KEYWORDS):
        score += 3
    if any(token in lowered for token in PRIORITY_REASON_TOKENS):
        score += 2
    score += min(len(reason) // 40, 2)
    return score


def _collapse_component_duplicates(findings: List[FindingCandidate]) -> List[FindingCandidate]:
    best_by_component: dict[tuple[str, int], tuple[FindingCandidate, int]] = {}
    ordered_keys: List[tuple[str, int]] = []
    for cand in findings:
        key = (cand.component.casefold(), cand.level)
        score = _score_reason_value(cand.reason)
        current = best_by_component.get(key)
        if current is None:
            best_by_component[key] = (cand, score)
            ordered_keys.append(key)
            continue
        best_cand, best_score = current
        if score > best_score or (score == best_score and len(cand.reason) > len(best_cand.reason)):
            best_by_component[key] = (cand, score)
    return [best_by_component[key][0] for key in ordered_keys]


def _candidates_from_segments(segments: List[Segment]) -> List[FindingCandidate]:
    findings: List[FindingCandidate] = []
    total = len(segments)
    index = 0

    while index < total:
        segment = segments[index]
        text = segment.text.strip()
        if not text:
            index += 1
            continue

        level, trigger_kind = _detect_level(text)
        if trigger_kind == "summary":
            index += 1
            continue
        if level is None:
            index += 1
            continue

        if _should_skip_tg_entry(text):
            index += 1
            continue

        reason_segments: List[Segment] = []

        if trigger_kind == "tg":
            inline_reason = _strip_tg_marker(segment.original_text or segment.text)
        else:
            inline_reason = ""

        if inline_reason:
            inline_check = inline_reason.casefold().strip(" .:;-")
            if inline_check in {"avvik som kan kreve tiltak", "store eller alvorlige avvik"}:
                inline_reason = ""

        if inline_reason:
            reason_segments.append(
                Segment(
                    text=inline_reason,
                    kilde_side=segment.kilde_side,
                    original_text=inline_reason,
                )
            )

        follower_idx = index + 1
        while follower_idx < total:
            follower = segments[follower_idx]
            follower_text = follower.text.strip()
            if not follower_text:
                break
            follower_level, follower_kind = _detect_level(follower_text)
            if follower_level is not None or follower_kind == "summary":
                break
            if _is_section_break(follower_text):
                break
            reason_segments.append(follower)
            follower_idx += 1
            if _ends_reason(follower_text):
                break

        summary_candidates = _extract_summary_candidates(segment, reason_segments, level)
        if summary_candidates:
            findings.extend(summary_candidates)
            if reason_segments and follower_idx > index + 1:
                index = follower_idx
            else:
                index += 1
            continue

        if reason_segments:
            reason_text = _combine_reason(reason_segments)
            component = _infer_component(reason_text, segments, index, reason_segments)
            if reason_text and not _looks_like_definition(reason_text):
                cleaned_reason = _clean_reason_text(reason_text)
                if cleaned_reason and _is_relevant_reason(
                    cleaned_reason,
                    component=component,
                    original=reason_text,
                ):
                    findings.append(
                        FindingCandidate(
                            component=component,
                            reason=cleaned_reason,
                            level=level,
                            kilde_side=segment.kilde_side,
                        )
                    )
                else:
                    fallback_reason, fallback_source, fallback_original, fallback_component_match = _fallback_reason_from_previous(
                        segments,
                        index,
                        component,
                    )
                    if fallback_reason and _is_relevant_reason(
                        fallback_reason,
                        component=component,
                        original=fallback_original,
                    ):
                        fallback_component = component
                        if fallback_component_match:
                            if component == "Ukjent" or fallback_component_match == component:
                                fallback_component = fallback_component_match
                        findings.append(
                            FindingCandidate(
                                component=fallback_component,
                                reason=fallback_reason,
                                level=level,
                                kilde_side=fallback_source or segment.kilde_side,
                            )
                        )

        if reason_segments and follower_idx > index + 1:
            index = follower_idx
        else:
            index += 1

    return findings


def _normalise_original_text(value: str) -> str:
    if not value:
        return ""
    return value.strip("\ufeff \t\r\n")


TG_INLINE_RX = re.compile(r"(?<!\w)TG\s*[:\-]?\s*([23])[:\-\s\.]*", re.IGNORECASE)
SECTION_BREAK_KEYWORDS = (
    "INFORMASJON",
    "VEDLEGG",
    "BOLIGSALGSRAPPORT",
    "TILSTANDSRAPPORT",
    "OPPDRAG",
    "BOLIGSELGERFORSIKRING",
    "BUDREGLEMENT",
    "BILDER",
    "INNHOLD",
    "SIDE",
)


def _strip_tg_marker(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    match = TG_INLINE_RX.search(cleaned)
    if not match:
        return ""
    start, end = match.span()
    prefix = cleaned[:start].strip(" -—–:•.;")
    suffix = cleaned[end:].strip()
    if not suffix and not prefix:
        return ""
    marker_segment = cleaned[start:end]
    if prefix and suffix:
        if "-" in marker_segment:
            joiner = " - "
        elif ":" in marker_segment:
            joiner = ": "
        else:
            joiner = " "
        stripped = f"{prefix}{joiner}{suffix}"
    elif suffix:
        stripped = suffix
    else:
        stripped = prefix
    return stripped.strip(" -—–:•.;")


def _is_section_break(text: str) -> bool:
    if not text:
        return True
    upper = text.upper()
    if any(keyword in upper for keyword in SECTION_BREAK_KEYWORDS):
        return True
    if re.match(r"^\s*\d+[\.\)]", text):
        return True
    return False


def _ends_reason(text: str) -> bool:
    return False


def _combine_reason(reason_segments: List[Segment]) -> str:
    parts: List[str] = []
    for seg in reason_segments:
        raw = seg.original_text or seg.text
        cleaned = WHITESPACE_RX.sub(" ", raw.strip())
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts).strip()


def _extract_summary_candidates(
    trigger: Segment,
    reason_segments: List[Segment],
    level: int,
) -> List[FindingCandidate]:
    trigger_text = (trigger.original_text or trigger.text or "").casefold()
    if "forhold som har fått" not in trigger_text and "bygningsdeler med" not in trigger_text:
        return []

    findings: List[FindingCandidate] = []
    total = len(reason_segments)
    i = 0
    while i < total:
        segment = reason_segments[i]
        text = segment.text.strip()
        lowered = text.casefold()
        if "oppsummering" not in lowered:
            i += 1
            continue

        component_label = _extract_component_label(text) or ""
        component_match = _find_component(component_label)
        component = component_match.component if component_match else component_label or "Ukjent"

        reason_lines: List[Segment] = []
        j = i + 1
        while j < total:
            candidate = reason_segments[j]
            candidate_text = candidate.text.strip()
            candidate_lower = candidate_text.casefold()
            if not candidate_text:
                j += 1
                continue
            if "oppsummering" in candidate_lower:
                break
            candidate_level, _ = _detect_level(candidate_text)
            if candidate_level is not None:
                break
            if _is_section_break(candidate_text):
                break
            reason_lines.append(candidate)
            if "." in candidate_text or len(reason_lines) >= 2:
                break
            j += 1
            continue
        if reason_lines:
            j = max(j, i + 1 + len(reason_lines))
        if reason_lines:
            combined = _combine_reason(reason_lines)
            cleaned = _clean_reason_text(combined)
            if cleaned and _is_relevant_reason(cleaned, component=component, original=combined):
                findings.append(
                    FindingCandidate(
                        component=component,
                        reason=cleaned,
                        level=level,
                        kilde_side=reason_lines[0].kilde_side,
                    )
                )
        i = j if reason_lines else i + 1
    return findings


def _fallback_reason_from_previous(
    all_segments: List[Segment],
    tg_index: int,
    component: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    start = max(0, tg_index - 12)
    best_reason: str | None = None
    best_source: str | None = None
    best_original: str | None = None
    best_component: str | None = None
    best_score = -1
    for idx in range(tg_index - 1, start - 1, -1):
        prev = all_segments[idx]
        text = prev.text.strip()
        if not text:
            continue
        level, _ = _detect_level(text)
        if level is not None:
            break
        if _is_section_break(text):
            break
        if "oppsummering" in text.casefold():
            continue
        if text.casefold().startswith("er det"):
            continue
        cleaned = _clean_reason_text(text)
        if not cleaned:
            continue
        if not _is_relevant_reason(cleaned, component=component, original=text):
            continue
        component_match = _find_component(cleaned)
        keyword_hit = _contains_any(cleaned.casefold(), IMPORTANT_REASON_KEYWORDS)
        score = 1
        if keyword_hit:
            score += 2
        lowered_cleaned = cleaned.casefold()
        if "fall" in lowered_cleaned and "sluk" in lowered_cleaned:
            score += 2
        if "bom" in lowered_cleaned:
            score += 1
        if "mangler" in lowered_cleaned:
            score += 1
        if component_match:
            if component_match.component == component:
                score += 2
            else:
                score += 1
        if score > best_score or (
            score == best_score and (best_reason is None or len(cleaned) > len(best_reason))
        ):
            best_reason = cleaned
            best_source = prev.kilde_side
            best_original = text
            best_component = component_match.component if component_match else None
            best_score = score
            if component_match and component_match.component == component and score >= 5:
                break
    return best_reason, best_source, best_original, best_component


def _infer_component(
    reason_text: str,
    all_segments: List[Segment],
    tg_index: int,
    reason_segments: List[Segment],
) -> str:
    component_match = _find_component(reason_text)
    if component_match:
        return component_match.component

    for seg in reason_segments:
        label = _extract_component_label(seg.text)
        if not label:
            continue
        component_match = _find_component(label)
        if component_match:
            return component_match.component

    start = max(0, tg_index - 5)
    for back_idx in range(tg_index - 1, start - 1, -1):
        label = _extract_component_label(all_segments[back_idx].text)
        if not label:
            continue
        component_match = _find_component(label)
        if component_match:
            return component_match.component

    fallback_label = _extract_component_label(reason_segments[0].text if reason_segments else "")
    return fallback_label or "Ukjent"


def _extract_component_label(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip().lstrip("•-* ")
    if not cleaned:
        return None
    if ":" in cleaned:
        label = cleaned.split(":", 1)[0].strip()
    else:
        words = cleaned.split()
        if len(words) > 6:
            return None
        label = " ".join(words[:5]).strip()
    label = re.sub(r"\s*[-–—]+\s*\d+$", "", label).strip()
    return label or None


SKIP_TG_KEYWORDS = (
    "tilstandsgrad",
    "fordeling av",
    "anslag på utbedringskostnad",
    "anslag for utbedringskostnad",
    "ingen umiddelbare kostnader",
    "tg0",
    "tg1",
    "tg iu",
    "tg-iu",
    "avvik som ikke krever",
    "tilstandsgrader",
)


def _should_skip_tg_entry(text: str) -> bool:
    lowered = text.casefold()
    return any(keyword in lowered for keyword in SKIP_TG_KEYWORDS)


DEFINITION_KEYWORDS = (
    "tilstandsgrad",
    "bygningsdelen",
    "konstruksjoner som ikke er undersøkt",
    "fordeling av tilstandsgrader",
    "fordeling av",
    "anslag på utbedringskostnad",
    "anslag for utbedringskostnad",
    "boligbygg",
    "gå til side",
    "ingen umiddelbare kostnader",
    "tiltak mellom",
    "tiltak over",
    "tiltak under",
)


def _looks_like_definition(text: str) -> bool:
    lowered = text.casefold()
    return any(keyword in lowered for keyword in DEFINITION_KEYWORDS)


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
            raw_line = line.rstrip("\r\n")
            cleaned_line = raw_line.strip()
            if cleaned_line:
                segments.append(
                    Segment(
                        text=cleaned_line,
                        kilde_side=str(idx),
                        original_text=raw_line,
                    )
                )
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
            segments.append(
                Segment(
                    text=row_text,
                    kilde_side=label,
                    original_text=row_text,
                )
            )

    for tag in soup.find_all(["p", "li", "dd", "dt", "div", "span"]):
        if tag.find_parent("table"):
            continue
        text = tag.get_text(" ", strip=True)
        text = WHITESPACE_RX.sub(" ", text).strip()
        if text:
            segments.append(
                Segment(
                    text=text,
                    kilde_side=label,
                    original_text=text,
                )
            )

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
