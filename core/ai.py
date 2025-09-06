# ai.py
import os, streamlit as st
from openai import OpenAI


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
