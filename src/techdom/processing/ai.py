# core/ai.py
from __future__ import annotations

import os
import re
import json
from typing import Dict, Any, Iterable, List, Sequence, TypedDict
from openai import OpenAI


class Inputs(TypedDict, total=False):
    price: int
    equity: int
    interest: float
    term_years: int
    rent: int
    hoa: int


class Metrics(TypedDict, total=False):
    cashflow: float
    break_even: float
    noi_year: float
    total_equity_return_pct: float


def _local_explain(inputs: Inputs, m: Metrics) -> str:
    vurdering = "ok"
    ter = float(m.get("total_equity_return_pct", 0.0) or 0.0)
    if ter >= 7:
        vurdering = "god"
    if ter < 3:
        vurdering = "svak"
    return (
        f"**Vurdering:** {vurdering}. ROE {ter:.1f}%.\n\n"
        f"Cashflow {float(m.get('cashflow', 0.0)):.0f} kr/mnd, "
        f"break-even {float(m.get('break_even', 0.0)):.0f} kr/mnd."
    )


def _get_key() -> str:
    """Hent OpenAI-nøkkel fra miljøvariabler."""
    env_key = os.getenv("OPENAI_API_KEY") or ""
    return env_key


def _clean_question_subject(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[\-\*\u2022]+\s*", "", text)
    text = re.sub(r"\bTG\s*\d[:\-]?\s*", "", text, flags=re.I)
    text = re.sub(r"^\d+[\.\)]\s*", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip(" \t-–—:;.")
    if len(text) > 160:
        text = text[:157].rstrip(",;:- ") + "..."
    return text


def _generate_follow_up_questions(
    tg3: Iterable[str],
    watchouts: Iterable[str],
    tg2: Iterable[str],
    upgrades: Iterable[str],
    limit: int = 6,
) -> List[str]:
    templates = {
        "tg3": "Når utbedres «{item}», og hvem dekker kostnaden?",
        "watch": "Hva er risiko og neste steg for «{item}»?",
        "tg2": "Trengs tiltak snart for «{item}», og hva er kostnaden?",
        "upgrade": "Er det budsjettert for «{item}», og når gjøres det?",
        "fallback": "Kan dere utdype «{item}» og hvilke kostnader den medfører?",
    }
    questions: List[str] = []
    seen: set[str] = set()

    def add_question(category: str, raw: str) -> None:
        if len(questions) >= limit:
            return
        subject = _clean_question_subject(raw)
        if not subject:
            return
        key = subject.casefold()
        if key in seen:
            return
        template = templates.get(category) or templates["fallback"]
        questions.append(template.format(item=subject))
        seen.add(key)

    for item in tg3:
        add_question("tg3", item)
        if len(questions) >= limit:
            return questions

    for item in watchouts:
        add_question("watch", item)
        if len(questions) >= limit:
            return questions

    for item in tg2:
        add_question("tg2", item)
        if len(questions) >= limit:
            return questions

    for item in upgrades:
        add_question("upgrade", item)
        if len(questions) >= limit:
            return questions

    return questions


def ai_explain(inputs: Inputs, m: Metrics) -> str:
    key = _get_key()
    if not key:
        return _local_explain(inputs, m)
    try:
        client = OpenAI(api_key=key)
        prompt = (
            f"Kort norsk analyse. Kjøpesum {int(inputs.get('price', 0)):,}, "
            f"EK {int(inputs.get('equity', 0)):,}, "
            f"rente {float(inputs.get('interest', 0.0))} %, "
            f"{int(inputs.get('term_years', 0))} år. "
            f"Leie {int(inputs.get('rent', 0)):,}/mnd, "
            f"HOA {int(inputs.get('hoa', 0)):,}/mnd. "
            f"Cashflow {float(m.get('cashflow', 0.0)):.0f}, "
            f"break-even {float(m.get('break_even', 0.0)):.0f}, "
            f"NOI {float(m.get('noi_year', 0.0)):.0f}, "
            f"ROE {float(m.get('total_equity_return_pct', 0.0)):.1f}%."
        )
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        content = (r.choices[0].message.content or "").strip()
        return content or _local_explain(inputs, m)
    except Exception:
        return _local_explain(inputs, m)


_PROSPECTUS_COMPONENT_TERMS: Sequence[str] = (
    "bad",
    "baderom",
    "vaskerom",
    "vatrm",
    "kjokken",
    "kjokkeninnredning",
    "kjeller",
    "loft",
    "tak",
    "taket",
    "taktekking",
    "takstein",
    "takbelegg",
    "pipe",
    "piper",
    "skorstein",
    "vindu",
    "vinduer",
    "dorer",
    "ytterdor",
    "ytterdoer",
    "innervegg",
    "yttervegg",
    "vegg",
    "vegger",
    "gulv",
    "bjelkelag",
    "grunnmur",
    "fundament",
    "drener",
    "radon",
    "ventilasjon",
    "avtrekk",
    "terrasse",
    "balkong",
    "veranda",
    "rekkverk",
    "trapp",
    "fasade",
    "kledning",
    "isolasjon",
    "mur",
    "betong",
    "puss",
    "sikringsskap",
    "elanlegg",
    "elektrisk",
    "elektro",
    "varmtvannsbereder",
    "bereder",
    "ror",
    "avlop",
    "avloppsror",
    "sanitar",
    "sluk",
    "membran",
    "vatrom",
    "garasje",
    "carport",
    "bod",
    "takstol",
    "bjaelke",
    "loftsbjelke",
    "nedlop",
    "takrenne",
    "renne",
    "yttertett",
    "tegl",
)

_PROSPECTUS_ISSUE_TERMS: Sequence[str] = (
    "ikke godkjent",
    "fukt",
    "fuktskade",
    "lekk",
    "rate",
    "raate",
    "mugg",
    "sopp",
    "skade",
    "skader",
    "sprekk",
    "sprekker",
    "defekt",
    "mangel",
    "avvik",
    "korrosjon",
    "rust",
    "utett",
    "svikt",
    "brudd",
    "fare",
    "risiko",
    "eldre",
    "gammel",
    "slitt",
    "slitasje",
    "oppgradering",
    "utbedring",
    "rehab",
    "oppussing",
    "avrenning",
    "setnings",
    "skjev",
    "ubehandlet",
    "sprukket",
    "manglende",
    "ukjent",
    "byttes",
    "bytte",
    "ma skiftes",
    "utskift",
    "brann",
    "brannfare",
    "kondens",
    "tett",
    "kondens",
)


def _simplify_text(value: str) -> str:
    text = value.casefold()
    return (
        text.replace("ø", "o")
        .replace("å", "a")
        .replace("æ", "ae")
        .replace("é", "e")
        .replace("ü", "u")
        .replace("ö", "o")
    )


def _prospectus_tokens(value: str) -> List[str]:
    return [token for token in re.split(r"[^a-z0-9]+", value) if token]


def _normalise_prospectus_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = re.sub(r"^[\-\*\u2022\u2043\u2219\u25cf]+\s*", "", text)
    text = re.sub(r"\btilstands?grad\s*\d\b[:\-–—\s]*", "", text, flags=re.I)
    text = re.sub(r"\bTG\s*[-/]*\s*(?:0|1|2|3|iu)\b[:\-–—\s]*", "", text, flags=re.I)
    text = re.sub(r"\bTG\s*(?:0|1|2|3)\b", "", text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip(" .,:;–—-")
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return text


def _looks_specific_issue(text: str) -> bool:
    if not text:
        return False
    normalised = _simplify_text(text)
    if len(normalised) < 8:
        return False
    tokens = set(_prospectus_tokens(normalised))

    def _contains(term: str) -> bool:
        if " " in term:
            return term in normalised
        return term in tokens

    has_component = any(_contains(term) for term in _PROSPECTUS_COMPONENT_TERMS)
    has_issue = any(_contains(term) for term in _PROSPECTUS_ISSUE_TERMS)
    if _contains("ikke godkjent"):
        has_issue = True
    if has_component and (has_issue or len(text.split()) >= 3):
        return True
    if has_issue and has_component:
        return True
    return False


def _dedupe_preserve_order(items: Iterable[str], limit: int) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        candidate = item.strip()
        if not candidate:
            continue
        key = _simplify_text(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate[:200])
        if len(result) >= limit:
            break
    return result


def _gather_issue_block(lines: Sequence[str], start: int) -> str:
    block_parts: List[str] = [lines[start]]
    for offset in range(1, 4):
        idx = start + offset
        if idx >= len(lines):
            break
        next_line = lines[idx]
        if re.search(r"\bTG\s*\d\b", next_line, re.I):
            break
        block_parts.append(next_line)
        if len(" ".join(block_parts)) > 300:
            break
    raw = " ".join(block_parts)
    return _normalise_prospectus_text(raw)


def _extract_tagged_issues(lines: Sequence[str], tag_regex: re.Pattern[str]) -> List[str]:
    collected: List[str] = []
    for index, line in enumerate(lines):
        if not tag_regex.search(line):
            continue
        snippet = _gather_issue_block(lines, index)
        if not snippet:
            continue
        if not _looks_specific_issue(snippet):
            continue
        collected.append(snippet)
    return _dedupe_preserve_order(collected, 10)


def _extract_watchout_issues(lines: Sequence[str], exclude: Sequence[str]) -> List[str]:
    collected: List[str] = []
    exclude_keys = {_simplify_text(item) for item in exclude}
    for index, line in enumerate(lines):
        simplified = _simplify_text(line)
        if not any(term in simplified for term in _PROSPECTUS_ISSUE_TERMS):
            continue
        snippet = _gather_issue_block(lines, index)
        if not snippet:
            continue
        key = _simplify_text(snippet)
        if key in exclude_keys:
            continue
        if not _looks_specific_issue(snippet):
            continue
        collected.append(snippet)
    return _dedupe_preserve_order(collected, 12)


def analyze_prospectus(text: str) -> Dict[str, Any]:
    """
    Returnerer et strukturert resultat:
    {
      "summary_md": str,
      "tg3": [str, ...],
      "tg2": [str, ...],
      "upgrades": [str, ...],
      "watchouts": [str, ...],
      "questions": [str, ...]
    }
    """
    text = (text or "").strip()
    if not text:
        return {
            "summary_md": "_Lim inn tekst fra salgsoppgave/tilstandsrapport for analyse._",
            "tg3": [],
            "tg2": [],
            "upgrades": [],
            "watchouts": [],
            "questions": [],
        }

    key = _get_key()
    if key:
        try:
            client = OpenAI(api_key=key)
            system = (
                "Du er en norsk eiendomsanalytiker. Ekstraher kort og tydelig liste over TG3, TG2, "
                "hva som bør pusses opp, viktige risikopunkter og forslag til spørsmål til megler. "
                "Svar KUN som JSON i følgende format med korte bulletpunkter (maks 12 ord per punkt): "
                '{"summary_md":"...","tg3":["..."],"tg2":["..."],"upgrades":["..."],"watchouts":["..."],"questions":["..."]}'
            )
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                max_tokens=700,
            )
            raw = r.choices[0].message.content or "{}"
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                obj = {}
            # defensiv normalisering
            out: Dict[str, Any] = {
                "summary_md": str(obj.get("summary_md") or ""),
                "tg3": [str(x) for x in (obj.get("tg3") or [])],
                "tg2": [str(x) for x in (obj.get("tg2") or [])],
                "upgrades": [str(x) for x in (obj.get("upgrades") or [])],
                "watchouts": [str(x) for x in (obj.get("watchouts") or [])],
            }
            out["questions"] = _generate_follow_up_questions(
                out["tg3"], out["watchouts"], out["tg2"], out["upgrades"]
            )
            return out
        except Exception:
            # faller til regex-basert
            pass

    # --- Fallback uten OpenAI: enkel regex-plukk ---
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    tg3 = _extract_tagged_issues(lines, re.compile(r"\bTG\s*3\b", re.I))
    tg2 = _extract_tagged_issues(lines, re.compile(r"\bTG\s*2\b", re.I))

    upgrades_candidates = [
        _normalise_prospectus_text(line)
        for line in lines
        if re.search(r"(oppgrad|rehab|utbedr|pusse)", line, re.I)
    ]
    upgrades = _dedupe_preserve_order(upgrades_candidates, 8)
    watchouts = _extract_watchout_issues(lines, exclude=[*tg2, *tg3])
    questions = _generate_follow_up_questions(tg3, watchouts, tg2, upgrades)
    return {
        "summary_md": (
            "Funn basert på enkel tekstskanning (begrenset uten AI-nøkkel). "
            "Se TG-punkter og risikopunkter under."
        ),
        "tg3": tg3[:10],
        "tg2": tg2[:10],
        "upgrades": upgrades[:8],
        "watchouts": watchouts[:8],
        "questions": questions,
    }
