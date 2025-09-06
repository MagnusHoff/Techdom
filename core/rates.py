# core/rates.py
from __future__ import annotations
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

# --- Konfig (kan overrides via .env) ---
DNB_URL_DEFAULT = "https://www.dnb.no/privat/lan/boliglan/priser-boliglan"
NORGES_BANK_URL_DEFAULT = (
    "https://www.norges-bank.no/tema/pengepolitikk/styringsrenten/"
)

START_MARGIN_DEFAULT = float(
    os.getenv("RATE_START_MARGIN", "1.25")
)  # realistisk startmargin
POLICY_FALLBACK_DEFAULT = float(os.getenv("POLICY_RATE_FALLBACK", "4.50"))

TTL_DNB_SECONDS = int(os.getenv("TTL_DNB_DAYS", "7")) * 24 * 3600  # cache DNB i 7 dager
TTL_POLICY_SECONDS = (
    int(os.getenv("TTL_POLICY_HOURS", "24")) * 3600
)  # cache styringsrente i 24 t

CACHE_FILE = Path("data/rate_cache.json")
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; TechdomAI/1.0; +https://example.com)",
    "Accept-Language": "no,en;q=0.9",
    "Cache-Control": "no-cache",
}


@dataclass
class RateMeta:
    source: str  # "dnb" eller "policy+margin"
    dnb_rate: Optional[float]
    policy_rate: Optional[float]
    margin_used: Optional[float]
    calibrated_at: Optional[str]  # ISO string eller None


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(data: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _now() -> int:
    return int(time.time())


def _within(ts: Optional[int], ttl: int) -> bool:
    return bool(ts and (_now() - ts) < ttl)


def _http_get(url: str, timeout: int = 10) -> Optional[str]:
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception:
        return None
    return None


def _extract_percent_candidates(text: str) -> list[float]:
    """
    Finn alle prosenter i tekst som ser ut som '5,49 %' eller '5.49%'.
    Filtrer til fornuftige boliglånsområder [2, 10] (for å unngå 0,1% osv).
    Returnerer liste i float (desimalpunkt).
    """
    nums = []
    for m in re.finditer(r"(\d{1,2}[.,]\d{1,2})\s*%", text):
        s = m.group(1).replace(",", ".")
        try:
            v = float(s)
            if 2.0 <= v <= 10.0:
                nums.append(v)
        except Exception:
            continue
    # også plukk heltall med % (f.eks. '6 %')
    for m in re.finditer(r"\b(\d{1,2})\s*%", text):
        try:
            v = float(m.group(1))
            if 2.0 <= v <= 10.0:
                nums.append(v)
        except Exception:
            continue
    return nums


def fetch_dnb_mortgage_rate() -> Optional[Tuple[float, str]]:
    """
    Skrap DNBs boliglånsrente (veiledende). Returnerer (rate, iso_timestamp) eller None.
    Strategi: hent side, plukk ut alle %-tall 2–10, ta median – robust mot 'effektiv' vs 'nominell'.
    """
    url = os.getenv("DNB_MORTGAGE_URL", DNB_URL_DEFAULT)
    html = _http_get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    cands = _extract_percent_candidates(text)
    if not cands:
        return None
    cands.sort()
    mid = len(cands) // 2
    median = cands[mid] if len(cands) % 2 == 1 else (cands[mid - 1] + cands[mid]) / 2
    return (round(median, 2), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def fetch_policy_rate() -> Optional[Tuple[float, str]]:
    """
    Hent styringsrenten fra Norges Bank sin infoside (ikke API for enkelhet).
    Plukker første prosent mellom 0–10 %. Returnerer (rate, iso_timestamp) eller None.
    """
    url = os.getenv("NORGES_BANK_POLICY_URL", NORGES_BANK_URL_DEFAULT)
    html = _http_get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    # plukk første fornuftige prosent
    cands = _extract_percent_candidates(text)
    if not cands:
        return None
    rate = round(cands[0], 2)
    return (rate, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def _get_cached(name: str) -> tuple[Optional[float], Optional[str], Optional[int]]:
    c = _load_cache()
    d = c.get(name) or {}
    return d.get("value"), d.get("timestamp"), d.get("ts")


def _set_cached(name: str, value: float, iso: str) -> None:
    c = _load_cache()
    c[name] = {"value": value, "timestamp": iso, "ts": _now()}
    _save_cache(c)


def get_interest_estimate(return_meta: bool = False) -> float | Tuple[float, RateMeta]:
    """
    HYBRID:
      1) Prøv DNB direkte → hvis ok, returner den (og kalibrer margin om mulig).
      2) Ellers: styringsrente + margin (sist kalibrert, ellers startmargin).
    Cacher DNB (7d) og NR (24t). Lagre margin når begge tilgjengelig.
    """
    cache = _load_cache()

    # 1) PRØV DNB (cache først)
    dnb_val, dnb_iso, dnb_ts = _get_cached("dnb_rate")
    if not _within(dnb_ts, TTL_DNB_SECONDS):
        got = fetch_dnb_mortgage_rate()
        if got:
            dnb_val, dnb_iso = got
            _set_cached("dnb_rate", dnb_val, dnb_iso)
    # 2) POLICYRATE (cache først)
    pol_val, pol_iso, pol_ts = _get_cached("policy_rate")
    if not _within(pol_ts, TTL_POLICY_SECONDS):
        gotp = fetch_policy_rate()
        if gotp:
            pol_val, pol_iso = gotp
            _set_cached("policy_rate", pol_val, pol_iso)

    # Margin i cache
    margin_val = cache.get("margin", {}).get("value")
    margin_iso = cache.get("margin", {}).get("timestamp")

    # Hvis vi har både DNB og policy nå → kalibrer margin
    if dnb_val is not None and pol_val is not None:
        new_margin = round(dnb_val - pol_val, 2)
        _set_cached(
            "margin",
            new_margin,
            dnb_iso or pol_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        margin_val, margin_iso, _ = _get_cached("margin")

    # Hvis DNB finnes nå (fersk eller cachet) → bruk den direkte
    if dnb_val is not None:
        meta = RateMeta(
            source="dnb",
            dnb_rate=dnb_val,
            policy_rate=pol_val,
            margin_used=None,
            calibrated_at=margin_iso,
        )
        return (dnb_val, meta) if return_meta else dnb_val

    # Ellers: policy + margin (fallbacks)
    policy = pol_val if pol_val is not None else POLICY_FALLBACK_DEFAULT
    margin = margin_val if margin_val is not None else START_MARGIN_DEFAULT
    estimate = round(policy + margin, 2)
    meta = RateMeta(
        source="policy+margin",
        dnb_rate=None,
        policy_rate=policy,
        margin_used=margin,
        calibrated_at=margin_iso,
    )
    return (estimate, meta) if return_meta else estimate
