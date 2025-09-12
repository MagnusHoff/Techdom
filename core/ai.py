# ai.py
import os, streamlit as st
from openai import OpenAI
import re, os
import json
from typing import Dict, Any, List


def _local_explain(inputs, m):
    vurdering = "ok"
    if m["total_equity_return_pct"] >= 7:
        vurdering = "god"
    if m["total_equity_return_pct"] < 3:
        vurdering = "svak"
    return (
        f"**Vurdering:** {vurdering}. ROE {m['total_equity_return_pct']:.1f}%.\n\n"
        f"Cashflow {m['cashflow']:.0f} kr/mnd, break-even {m['break_even']:.0f} kr/mnd."
    )


def _get_key():
    return os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY", "")


def ai_explain(inputs, m):
    key = _get_key()
    if not key:
        return _local_explain(inputs, m)
    try:
        client = OpenAI(api_key=key)
        prompt = (
            f"Kort norsk analyse. Kjøpesum {inputs['price']:,}, EK {inputs['equity']:,}, "
            f"rente {inputs['interest']} %, {inputs['term_years']} år. "
            f"Leie {inputs['rent']:,}/mnd, HOA {inputs['hoa']:,}/mnd. "
            f"Cashflow {m['cashflow']:.0f}, break-even {m['break_even']:.0f}, "
            f"NOI {m['noi_year']:.0f}, ROE {m['total_equity_return_pct']:.1f}%."
        )
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return _local_explain(inputs, m)


def _get_key():
    return os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY", "")


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
            from openai import OpenAI

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
            obj = json.loads(r.choices[0].message.content)
            # defensiv normalisering
            for k in ("tg3", "tg2", "upgrades", "watchouts", "questions"):
                obj[k] = [str(x) for x in (obj.get(k) or [])]
            obj["summary_md"] = obj.get("summary_md") or ""
            return obj
        except Exception:
            pass

    # --- Fallback uten OpenAI: enkel regex-plukk ---
    # Plukk linjer som nevner TG3/TG 3 / TG2 / TG 2
    tg3 = []
    tg2 = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in lines:
        if re.search(r"\bTG\s*3\b", l, re.I):
            tg3.append(l[:200])
        elif re.search(r"\bTG\s*2\b", l, re.I):
            tg2.append(l[:200])

    # naive forslag
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
