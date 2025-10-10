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
    ("Pipe/ildsted", ("pipe", "ildsted", "skorstein", "pipelop", "pipeløp", "peis", "skorste")),
]

WHITESPACE_RX = re.compile(r"\s+")

IMPORTANT_COMPONENT_TOKENS = (
    "bad",
    "våtrom",
    "vatrom",
    "membran",
    "drener",
    "grunnmur",
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
    "grunnmur",
    "kjeller",
    "membran",
    "sluk",
    "fall mot sluk",
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


def _build_summary_label(component: str, reason: str, level: int) -> str:
    words = _tokenise_summary_words(component)
    if len(words) < 2:
        words.extend(_tokenise_summary_words(reason))
    filtered = [word for word in words if word and not _is_tg_token(word, level)]
    if not filtered:
        filtered = words or [f"TG{level}"]
    deduped: List[str] = []
    seen: set[str] = set()
    for token in filtered:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    if not deduped:
        deduped = filtered
    label = " ".join(deduped[:3])
    return label.capitalize()


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


def _tokenise_summary_words(value: str) -> List[str]:
    if not value:
        return []
    normalized = _strip_diacritics(value.lower())
    tokens = re.findall(r"[a-z0-9æøå]+", normalized)
    return [token for token in tokens if token]


def _is_tg_token(token: str, level: int) -> bool:
    lowered = token.lower()
    return lowered in {"tg", f"tg{level}", "tilstandsgrad"}


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
        return f"Side {cleaned}"
    if lowered.startswith("side"):
        return cleaned.capitalize()
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
    """Remove duplicates while keeping the most informative candidate."""

    deduped: List[FindingCandidate] = []
    index_map: dict[tuple[str, str, int], int] = {}
    for cand in candidates:
        component_key = cand.component.casefold()
        reason_key = WHITESPACE_RX.sub(" ", cand.reason.casefold()).strip()
        key = (component_key, reason_key, cand.level)
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

    reason_map: dict[tuple[str, int], int] = {}
    reduced: List[FindingCandidate] = []
    for cand in deduped:
        reason_key = WHITESPACE_RX.sub(" ", cand.reason.casefold()).strip()
        reason_key_tuple = (reason_key, cand.level)
        existing_index = reason_map.get(reason_key_tuple)
        if existing_index is None:
            reason_map[reason_key_tuple] = len(reduced)
            reduced.append(cand)
            continue

        existing = reduced[existing_index]
        preferred = _prefer_candidate_for_reason(existing, cand)
        reduced[existing_index] = preferred

    return reduced


def _prefer_candidate_for_reason(
    existing: FindingCandidate, cand: FindingCandidate
) -> FindingCandidate:
    if cand.level > existing.level:
        return cand
    if cand.level < existing.level:
        return existing

    existing_in_reason = _component_matches_reason(existing)
    candidate_in_reason = _component_matches_reason(cand)

    if existing_in_reason and not candidate_in_reason:
        return existing
    if candidate_in_reason and not existing_in_reason:
        return cand

    existing_unknown = _is_unknown_component(existing.component)
    candidate_unknown = _is_unknown_component(cand.component)

    if existing_unknown and not candidate_unknown:
        return cand
    if candidate_unknown and not existing_unknown:
        return existing

    if len(cand.component) > len(existing.component):
        return cand
    return existing


def _component_matches_reason(candidate: FindingCandidate) -> bool:
    component = candidate.component.strip()
    if not component or _is_unknown_component(component):
        return False
    reason = WHITESPACE_RX.sub(" ", candidate.reason.casefold())
    component_key = WHITESPACE_RX.sub(" ", component.casefold())
    if not component_key:
        return False
    return component_key in reason


def _is_unknown_component(value: str) -> bool:
    return value.strip().casefold() in {"", "ukjent"}


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
