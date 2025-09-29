# core/history.py
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json, uuid, tempfile, shutil

from core import counters


_LAST_KNOWN_TOTAL: int | None = None

HISTORY_PATH = Path("data/analysis_history.jsonl")
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _load_all() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def _save_all(items: list[dict]) -> None:
    # atomisk skriving
    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    try:
        for rec in items:
            tmp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp.flush()
        shutil.move(tmp.name, HISTORY_PATH)
    finally:
        try:
            tmp.close()
        except Exception:
            pass


def add_analysis(
    *,
    finn_url: str,
    title: str,
    price: int | float | None = None,
    summary: str = "",
    image: str | None = None,
    result_args: dict | None = None,
) -> str:
    """
    Lagrer/oppdaterer en analyse. Duplikater (samme finn_url) fjernes – nyeste beholdes.
    'title' forventes å være adressen (vi lager ingen egen 'address'-felt).
    """
    global _LAST_KNOWN_TOTAL
    items = _load_all()
    # fjern eldre med samme URL
    items = [r for r in items if r.get("finn_url") != finn_url]

    analysis_id = str(uuid.uuid4())
    rec = {
        "id": analysis_id,
        "ts": _to_iso(datetime.now(timezone.utc)),
        "finn_url": finn_url,
        "title": title,
        "price": price,
        "summary": summary,
        "image": image,
        "result_args": result_args or {},
    }
    items.append(rec)
    # sorter nyeste først
    items.sort(key=lambda r: r.get("ts", ""), reverse=True)
    _save_all(items)
    try:
        new_total = counters.increment_total_count()
        if isinstance(new_total, int):
            _LAST_KNOWN_TOTAL = new_total
    except Exception:
        pass
    return analysis_id


def get_recent(n: int = 6) -> list[dict]:
    items = _load_all()
    # items er allerede tids-sortert i add_analysis, men sorter uansett defensivt
    items.sort(key=lambda r: r.get("ts", ""), reverse=True)
    # dedupe by URL i tilfelle eldre filer eksisterer
    seen = set()
    out = []
    for rec in items:
        u = rec.get("finn_url")
        if u in seen:
            continue
        seen.add(u)
        out.append(rec)
        if len(out) >= n:
            break
    return out


def get_total_count() -> int:
    """Returner totalt antall analyser, preferer ekstern teller hvis mulig."""
    global _LAST_KNOWN_TOTAL
    try:
        default = _LAST_KNOWN_TOTAL if _LAST_KNOWN_TOTAL is not None else -1
        external = counters.fetch_total_count(default=default)
    except Exception:
        external = _LAST_KNOWN_TOTAL if _LAST_KNOWN_TOTAL is not None else -1
    if isinstance(external, int) and external >= 0:
        _LAST_KNOWN_TOTAL = external
        return external
    if _LAST_KNOWN_TOTAL is not None:
        return _LAST_KNOWN_TOTAL
    return len(_load_all())
