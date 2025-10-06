# core/ai.py
from __future__ import annotations

import os
import re
import json
from typing import Dict, Any, List, TypedDict
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
                "questions": [str(x) for x in (obj.get("questions") or [])],
            }
            return out
        except Exception:
            # faller til regex-basert
            pass

    # --- Fallback uten OpenAI: enkel regex-plukk ---
    tg3: List[str] = []
    tg2: List[str] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in lines:
        if re.search(r"\bTG\s*3\b", l, re.I):
            tg3.append(l[:200])
        elif re.search(r"\bTG\s*2\b", l, re.I):
            tg2.append(l[:200])

    upgrades = [
        l for l in lines if re.search(r"(oppgrad|rehab|utbedr|pusse)", l, re.I)
    ][:8]
    watch = [
        l
        for l in lines
        if re.search(
            r"(fukt|råte|lekk|skade|avvik|mangel|asbest|radon|drenering|el-anlegg)",
            l,
            re.I,
        )
    ][:8]
    questions = [
        "Dokumentasjon på utførte arbeider og samsvar (FDV/kvitteringer)?",
        "Tilstandsrapport: detaljer for TG3/TG2 og estimerte kostnader?",
        "Alder/tilstand på tak, drenering, våtrom og el-anlegg?",
        "Avvik i felleskostnader/vedtekter, planlagte rehabiliteringer?",
    ]
    return {
        "summary_md": (
            "Funn basert på enkel tekstskanning (begrenset uten AI-nøkkel). "
            "Se TG-punkter og risikopunkter under."
        ),
        "tg3": tg3[:10],
        "tg2": tg2[:10],
        "upgrades": upgrades[:8],
        "watchouts": watch[:8],
        "questions": questions,
    }
