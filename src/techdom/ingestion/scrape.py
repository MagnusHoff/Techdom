# core/scrape.py
from __future__ import annotations

import io
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, Optional, List, Tuple, Any, TypedDict, cast
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from PyPDF2 import PdfReader, PdfWriter  # fallback + trimming

from techdom.ingestion.http_headers import BROWSER_HEADERS
from techdom.ingestion.sessions import new_session  # <-- felles session-oppsett


# ──────────────────────────────────────────────────────────────────────────────
#  Requests / HTML (delt session)
# ──────────────────────────────────────────────────────────────────────────────
def fetch_html(
    url: str, *, sess: requests.Session | None = None, timeout: int = 15
) -> str:
    """
    Hent HTML med felles new_session()-oppsett for stabil UA/proxy/cookies.
    """
    s = sess or new_session()
    r = s.get(url, headers=BROWSER_HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


# ──────────────────────────────────────────────────────────────────────────────
#  Små helpers
# ──────────────────────────────────────────────────────────────────────────────
def _attr_to_str(val: Any) -> str | None:
    """BeautifulSoup-attributter kan være lister. Normaliser til str|None."""
    if val is None:
        return None
    try:
        if isinstance(val, (list, tuple)):
            if not val:
                return None
            val = val[0]
        s = str(val).strip()
        return s or None
    except Exception:
        return None


def _abs(base_url: str, href: Any) -> str | None:
    if not href:
        return None
    try:
        return urljoin(base_url, str(href))
    except Exception:
        return None


def _clean_url(u: str) -> str:
    """Dropp tracking/fragment og unescape JSON-escaped slashes."""
    try:
        u = u.replace("\\/", "/")
        p = urlparse(u)
        q = parse_qs(p.query)
        drop = {k for k in q if k.startswith("utm_") or k in {"gclid", "fbclid"}}
        kept = [(k, v) for k, v in q.items() if k not in drop]
        query = "&".join(f"{k}={v[0]}" for k, v in kept if v)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, query, ""))
    except Exception:
        return u


def _norm(s: str | None) -> str:
    return (s or "").lower().strip()


_WS_RE = re.compile(r"\s+")


def _collapse_whitespace(value: str) -> str:
    return _WS_RE.sub(" ", value.strip())


def _to_float(x: str | float | int | None) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(str(x).strip().replace(" ", "").replace(",", "."))
    except Exception:
        return None


def _strip_diacritics(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _norm_compact(value: str | None) -> str:
    base = _strip_diacritics(value or "")
    lowered = base.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "", lowered)
    return cleaned


def _norm_tokens(value: str | None) -> str:
    base = _strip_diacritics(value or "")
    lowered = base.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass(frozen=True)
class _ExtractionContext:
    url: str
    soup: BeautifulSoup
    attrs: Dict[str, str]
    json_blobs: List[Any]


@dataclass(frozen=True)
class _FieldSpec:
    name: str
    json_keys: Tuple[str, ...] = ()
    html_labels: Tuple[str, ...] = ()
    cleaner: Optional[Callable[[Any], Any]] = None
    json_getter: Optional[Callable[[_ExtractionContext], Any]] = None
    fallback: Optional[Callable[[_ExtractionContext], Any]] = None


# ──────────────────────────────────────────────────────────────────────────────
#  PDF-tekst fra bytes (debug/verdi-ekstraksjon)
# ──────────────────────────────────────────────────────────────────────────────
def extract_pdf_text_from_bytes(pdf_bytes: bytes, max_pages: int = 40) -> str:
    """
    Prøver PyMuPDF (fitz) først for mer robust tekst, faller tilbake til PyPDF2.
    """
    # 1) PyMuPDF (fitz)
    try:
        import fitz  # type: ignore

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            chunks: List[str] = []
            upto = min(doc.page_count, max_pages)
            for i in range(upto):
                try:
                    t = doc.load_page(i).get_text("text") or ""
                except Exception:
                    t = ""
                if t.strip():
                    chunks.append(t)
            if chunks:
                return "\n".join(chunks).strip()
    except Exception:
        pass

    # 2) PyPDF2 fallback
    try:
        bio = io.BytesIO(pdf_bytes)
        reader = PdfReader(bio)
        chunks = []
        upto = min(len(reader.pages), max_pages)
        for page in reader.pages[:upto]:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                chunks.append(t)
        return "\n".join(chunks).strip()
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────────
#  Refinement: trekk ut salgsoppgave-delen fra samle-PDF (valgfritt bruk)
# ──────────────────────────────────────────────────────────────────────────────
def refine_salgsoppgave_from_bundle(
    pdf_bytes: bytes,
) -> Tuple[bytes | None, Dict[str, Any]]:
    """
    Forsøker å trimme 'Vedlegg til salgsoppgave' slik at bare selve salgsoppgaven blir igjen.
    Return: (pdf_bytes_ren, meta)
    """
    meta: Dict[str, Any] = {}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        return None, {"error": f"read_fail:{e!r}"}

    n = len(reader.pages)
    meta["pages_total"] = n
    if n == 0:
        return None, {"error": "empty_pdf"}

    POS = [
        r"\bsalgsoppgav",
        r"\bprospekt",
        r"\bmegler",
        r"eiendom",
        r"adresser",
        r"fakta",
        r"innhold",
        r"om eiendommen",
        r"nabolag",
        r"beliggenhet",
        r"standard",
        r"bebyggelse",
        r"adkomst",
        r"kommunenr",
        r"gnr",
        r"bnr",
    ]
    NEG = [
        r"\btilstandsrapport",
        r"\begenerkl",
        r"\benergiattest",
        r"\bbudskjema",
        r"\bkommunale opplysninger",
        r"\bbygningstegninger",
        r"\bdokumentasjon",
        r"\bløsøre",
        r"\bvedlegg\b",
        r"\bmeglerpakke",
    ]
    POS_RX = [re.compile(rx, re.I) for rx in POS]
    NEG_RX = [re.compile(rx, re.I) for rx in NEG]

    scores: List[int] = []
    texts: List[str] = []
    for i in range(n):
        try:
            txt = reader.pages[i].extract_text() or ""
        except Exception:
            txt = ""
        texts.append(txt)
        lo = txt.lower()
        sc = sum(2 for rx in POS_RX if rx.search(lo)) - sum(
            4 for rx in NEG_RX if rx.search(lo)
        )
        sc += max(0, 5 - i)  # litt bias for tidlige sider
        scores.append(sc)

    cut_at: Optional[int] = None
    for i, txt in enumerate(texts):
        lo = txt.lower()
        if any(rx.search(lo) for rx in NEG_RX) and i >= 3:
            cut_at = i
            break

    end = min(cut_at if cut_at is not None else n, 80)

    # fallback: finn beste 20–40 siders vindu hvis starten ser rar ut
    if sum(1 for s in scores[: min(10, n)] if s > 0) <= 2:
        best_sum, best_range = -(10**9), (0, min(30, n))
        for w in (20, 30, 40):
            if w > n:
                continue
            for i in range(0, n - w + 1):
                ssum = sum(scores[i : i + w])
                if ssum > best_sum:
                    best_sum, best_range = ssum, (i, i + w)
        start, end = best_range
        if cut_at is not None and cut_at < start:
            end = cut_at

    writer = PdfWriter()
    out_pages = 0  # robust teller (noen PyPDF2-versjoner har ikke writer.pages)
    for i in range(0, max(1, end)):
        try:
            writer.add_page(reader.pages[i])
            out_pages += 1
        except Exception:
            continue

    buf = io.BytesIO()
    try:
        writer.write(buf)
        out = buf.getvalue()
        meta.update(
            {
                "pages_out": out_pages,
                "cut_at": cut_at,
                "scores_head": scores[:10],
            }
        )
        return out, meta
    except Exception as e:
        return None, {"error": f"write_fail:{e!r}"}


# ──────────────────────────────────────────────────────────────────────────────
#  FINN-attributter (for areal/rom osv.)
# ──────────────────────────────────────────────────────────────────────────────
_M2_RX = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m²|m2|m\^2|kvm|kvadrat)", re.IGNORECASE)


def _parse_m2_from_text(txt: str | None) -> Optional[float]:
    if not txt:
        return None
    m = _M2_RX.search(txt)
    return _to_float(m.group(1)) if m else None


def _kv(txt: str | None) -> Optional[Tuple[str, str]]:
    if not txt:
        return None
    m = re.match(r"\s*([^:]{3,}):\s*(.+)\s*$", txt)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    m = re.match(r"\s*([A-Za-zÆØÅæøå0-9()\-\/\. ]{3,}?)\s{2,}(.+)\s*$", txt)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return None


def _find_by_class_substring(node: Tag, substring: str) -> Optional[Tag]:
    target = substring.lower()
    for descendant in node.find_all(True):
        classes = descendant.get("class") or []
        if any(target in cls.lower() for cls in classes if isinstance(cls, str)):
            return descendant
    return None


def _iter_attr_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(entry).lower() for entry in value if entry is not None]
    return [str(value).lower()]


def _find_by_attr_substring(node: Tag, attr: str, substring: str) -> Optional[Tag]:
    target = substring.lower()
    for descendant in node.find_all(True):
        values = _iter_attr_values(descendant.get(attr))
        if any(target in candidate for candidate in values):
            return descendant
    return None


def _has_attr_substring(node: Tag, attr: str, substring: str) -> bool:
    values = _iter_attr_values(node.get(attr))
    target = substring.lower()
    return any(target in candidate for candidate in values)


def _looks_like_key_info_container(node: Tag) -> bool:
    score = 0
    for descendant in node.find_all(True, limit=60):
        if not isinstance(descendant, Tag):
            continue
        name = descendant.name or ""
        if name in {"dl", "dt", "dd"}:
            return True
        data_testid_values = _iter_attr_values(descendant.get("data-testid"))
        if any("key" in value and "label" in value for value in data_testid_values):
            score += 1
        elif any("key" in value and "value" in value for value in data_testid_values):
            score += 1
        elif _has_attr_substring(descendant, "class", "label"):
            score += 1
        elif _has_attr_substring(descendant, "class", "value"):
            score += 1
        else:
            text = descendant.get_text(" ", strip=True)
            if _kv(text):
                score += 1
        if score >= 2:
            return True
    return False


def _find_key_info_section(soup: Any) -> Optional[Tag]:
    if not hasattr(soup, "find_all"):
        return None
    for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
        if not isinstance(heading, Tag):
            continue
        text = heading.get_text(" ", strip=True)
        if not text:
            continue
        if "nøkkelinfo" in text.lower():
            current: Optional[Tag] = heading
            while isinstance(current, Tag):
                if current.name == "section":
                    return current
                current = current.parent if isinstance(current.parent, Tag) else None
            parent = heading.parent
            return parent if isinstance(parent, Tag) else heading

    selectors = [
        "[data-testid*='key-info']",
        "[data-testid*='keyinfo']",
        "[data-testid*='key-fact']",
        "[data-testid*='keyfact']",
        "[class*='key-info']",
        "[class*='keyinfo']",
        "[class*='key-fact']",
        "[class*='keyfact']",
        "[class*='nokkelinfo']",
        "[class*='nøkkelinfo']",
        "[class*='nokkeltall']",
        "[class*='nøkkeltall']",
    ]
    for selector in selectors:
        if not hasattr(soup, "select"):
            break
        try:
            matches = soup.select(selector)
        except Exception:
            continue
        for candidate in matches:
            if not isinstance(candidate, Tag):
                continue
            if _looks_like_key_info_container(candidate):
                return candidate
    return None


def _extract_key_facts_raw(soup: Any) -> List[Dict[str, object]]:
    section = _find_key_info_section(soup)
    if not section:
        return []

    facts: List[Dict[str, object]] = []
    order = 0

    def _add(label_text: str | None, value_text: str | None) -> None:
        nonlocal order
        if not label_text or not value_text:
            return
        label_clean = _collapse_whitespace(label_text)
        value_clean = _collapse_whitespace(value_text)
        if not label_clean or not value_clean:
            return
        facts.append({"label": label_clean, "value": value_clean, "order": order})
        order += 1

    processed: set[int] = set()

    for node in section.find_all(True):
        if not isinstance(node, Tag):
            continue
        node_id = id(node)
        if node_id in processed:
            continue

        if node.name == "div":
            data_testid_values = _iter_attr_values(node.get("data-testid"))
            has_key_info_item = any(
                "key" in value and any(token in value for token in ("item", "row", "entry", "element"))
                for value in data_testid_values
            )
            if not has_key_info_item:
                class_values = _iter_attr_values(node.get("class"))
                has_key_info_item = any("key" in value and "item" in value for value in class_values)
            if has_key_info_item:
                label_node = (
                    _find_by_attr_substring(node, "data-testid", "label")
                    or _find_by_class_substring(node, "label")
                )
                value_node = (
                    _find_by_attr_substring(node, "data-testid", "value")
                    or _find_by_class_substring(node, "value")
                )
                if label_node and value_node:
                    _add(label_node.get_text(" ", strip=True), value_node.get_text(" ", strip=True))
                    processed.add(node_id)
                    processed.add(id(label_node))
                    processed.add(id(value_node))
                    for child in node.find_all(True):
                        processed.add(id(child))
                    continue
            dt = node.find("dt")
            dd = node.find("dd")
            if dt and dd:
                _add(dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))
                processed.update({node_id, id(dt), id(dd)})
                for child in dt.find_all(True):
                    processed.add(id(child))
                for child in dd.find_all(True):
                    processed.add(id(child))
                continue
            label_node = _find_by_class_substring(node, "label")
            value_node = _find_by_class_substring(node, "value")
            if label_node and value_node:
                _add(label_node.get_text(" ", strip=True), value_node.get_text(" ", strip=True))
                processed.add(node_id)
                for child in node.find_all(True):
                    processed.add(id(child))
                continue
            text = node.get_text(" ", strip=True)
            kv = _kv(text)
            if kv:
                _add(kv[0], kv[1])
                processed.add(node_id)
                continue

        if node.name == "dt":
            parent = node.parent if isinstance(node.parent, Tag) else None
            if parent and parent.name == "dl":
                sibling = node.find_next_sibling("dd")
                if sibling:
                    _add(node.get_text(" ", strip=True), sibling.get_text(" ", strip=True))
                    processed.update({node_id, id(sibling)})
                    for child in sibling.find_all(True):
                        processed.add(id(child))
                    continue

        if node.name == "li":
            if node.find_parent("dl"):
                continue
            label_node = _find_by_class_substring(node, "label")
            value_node = _find_by_class_substring(node, "value")
            if label_node and value_node:
                _add(label_node.get_text(" ", strip=True), value_node.get_text(" ", strip=True))
                processed.add(node_id)
                for child in node.find_all(True):
                    processed.add(id(child))
                continue
            text = node.get_text(" ", strip=True)
            kv = _kv(text)
            if kv:
                _add(kv[0], kv[1])
                processed.add(node_id)

    return facts


def _collect_attrs(soup: Any) -> Dict[str, str]:
    attrs: Dict[str, str] = {}

    # <dl><dt>/<dd>
    if hasattr(soup, "find_all"):
        for dl in soup.find_all("dl"):
            if not isinstance(dl, Tag):
                continue
            dts = [t for t in dl.find_all("dt") if isinstance(t, Tag)]
            dds = [t for t in dl.find_all("dd") if isinstance(t, Tag)]
            if dts and dds and len(dts) == len(dds):
                for dt, dd in zip(dts, dds):
                    k = (dt.get_text(" ", strip=True) or "").strip()
                    v = (dd.get_text(" ", strip=True) or "").strip()
                    if k and v and k not in attrs:
                        attrs[k] = v

    # tabeller
    if hasattr(soup, "find_all"):
        for table in soup.find_all("table"):
            if not isinstance(table, Tag):
                continue
            for tr in table.find_all("tr"):
                if not isinstance(tr, Tag):
                    continue
                tds = [t for t in tr.find_all(["th", "td"]) if isinstance(t, Tag)]
                if len(tds) >= 2:
                    k = (tds[0].get_text(" ", strip=True) or "").strip()
                    v = (tds[1].get_text(" ", strip=True) or "").strip()
                    if k and v and k not in attrs:
                        attrs[k] = v

    # diverse lister
    if hasattr(soup, "select"):
        for container in soup.select(
            "[data-testid*='object-facts'], [data-testid*='facts'], [class*='fact'], [class*='key'], [class*='info']"
        ):
            if not isinstance(container, Tag):
                continue
            for el in container.find_all(["li", "div", "span"]):
                if not isinstance(el, Tag):
                    continue
                txt = el.get_text(" ", strip=True)
                kv = _kv(txt)
                if kv:
                    k, v = kv
                    if k and v and k not in attrs:
                        attrs[k] = v
    return attrs


def _find_attr_value(
    attrs: Dict[str, str],
    include: List[str],
    exclude: Optional[List[str]] = None,
) -> Optional[str]:
    include_norm = [_norm(item) for item in include if item]
    exclude_norm = {_norm(item) for item in (exclude or []) if item}
    for key, value in attrs.items():
        key_norm = _norm(key)
        if not key_norm or key_norm in {"", "-"}:
            continue
        if any(block and block in key_norm for block in exclude_norm):
            continue
        for want in include_norm:
            if want and want in key_norm:
                return value
    return None


class KeyFactSpec(TypedDict, total=False):
    key: str
    include: List[str]
    exclude: List[str]
    group: str
    order: int
    parser: Callable[[str], Optional[object]]


class ExtraFact(TypedDict, total=False):
    key: str
    label: str
    value: Optional[object]
    group: str
    order: int


def _parse_currency_value(raw: str) -> Optional[int]:
    return _num(raw)


def _parse_area_value(raw: str) -> Optional[float]:
    return _parse_m2_from_text(raw)


def _parse_int_value(raw: str) -> Optional[int]:
    return _num(raw)


_KEY_FACT_SPECS: List[KeyFactSpec] = [
    {"key": "total_price", "include": ["totalpris"], "group": "Kostnader", "order": 10, "parser": _parse_currency_value},
    {"key": "asking_price", "include": ["prisantydning"], "group": "Kostnader", "order": 20, "parser": _parse_currency_value},
    {"key": "costs", "include": ["omkostninger"], "group": "Kostnader", "order": 30, "parser": _parse_currency_value},
    {
        "key": "hoa_month",
        "include": ["felleskostnader", "felleskost/mnd", "fellesutgifter"],
        "group": "Kostnader",
        "order": 40,
        "parser": _parse_currency_value,
    },
    {"key": "tax_value", "include": ["formuesverdi"], "group": "Kostnader", "order": 50, "parser": _parse_currency_value},
    {"key": "property_type", "include": ["boligtype", "type bolig"], "group": "Bolig", "order": 100},
    {"key": "ownership_type", "include": ["eieform"], "group": "Bolig", "order": 110},
    {
        "key": "rooms",
        "include": ["rom", "antall rom"],
        "exclude": ["soverom"],
        "group": "Planløsning",
        "order": 200,
        "parser": _parse_int_value,
    },
    {
        "key": "bedrooms",
        "include": ["soverom", "antall soverom"],
        "group": "Planløsning",
        "order": 210,
        "parser": _parse_int_value,
    },
    {
        "key": "internal_bra_m2",
        "include": ["internt bruksareal", "bra-i", "bra i"],
        "group": "Areal",
        "order": 300,
        "parser": _parse_area_value,
    },
    {
        "key": "primary_room_m2",
        "include": ["primærrom", "p-rom", "prom", "p rom"],
        "group": "Areal",
        "order": 310,
        "parser": _parse_area_value,
    },
    {
        "key": "bra_m2",
        "include": ["bruksareal", "bra totalt", "bra total", "bra"],
        "exclude": ["internt"],
        "group": "Areal",
        "order": 320,
        "parser": _parse_area_value,
    },
    {
        "key": "external_bra_m2",
        "include": ["eksternt bruksareal", "bra-e", "bra e"],
        "group": "Areal",
        "order": 330,
        "parser": _parse_area_value,
    },
    {
        "key": "balcony_terrace_m2",
        "include": ["balkong/terrasse", "balkong", "terrasse"],
        "group": "Areal",
        "order": 340,
        "parser": _parse_area_value,
    },
    {
        "key": "plot_area_m2",
        "include": ["tomteareal", "tomtestørrelse", "tomt"],
        "group": "Tomt",
        "order": 400,
        "parser": _parse_area_value,
    },
    {"key": "floor", "include": ["etasje"], "group": "Bygg", "order": 500, "parser": _parse_int_value},
    {"key": "built_year", "include": ["byggeår", "byggår", "byggeaar"], "group": "Bygg", "order": 510, "parser": _parse_int_value},
    {"key": "energy_label", "include": ["energimerking", "energi", "energikarakter"], "group": "Bygg", "order": 520},
]

_DEFAULT_FACT_ORDER = 1000


def _slugify_key(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "felt"


def _ensure_unique_key(base: str, seen: set[str]) -> str:
    key = base
    counter = 2
    while key in seen:
        key = f"{base}_{counter}"
        counter += 1
    seen.add(key)
    return key


def _pop_matching_attr(
    attrs: Dict[str, str],
    include: List[str],
    exclude: Optional[List[str]] = None,
) -> Optional[Tuple[str, str]]:
    include_norm = [_norm(item) for item in include if item]
    exclude_norm = {_norm(item) for item in (exclude or []) if item}
    for key in list(attrs.keys()):
        key_norm = _norm(key)
        if not key_norm or key_norm in {"", "-"}:
            continue
        if any(block and block in key_norm for block in exclude_norm):
            continue
        for want in include_norm:
            if want and want in key_norm:
                value = attrs.pop(key)
                return key, value
    return None


def _build_key_facts(
    attrs: Dict[str, str],
    extras: Optional[List[ExtraFact]] = None,
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    remaining = dict(attrs)
    facts: List[Dict[str, object]] = []
    derived: Dict[str, object] = {}
    seen_keys: set[str] = set()
    fallback_index = 0

    def _add_fact(label: str, raw_value: str, spec: Optional[KeyFactSpec] = None) -> None:
        nonlocal fallback_index
        raw_str = str(raw_value or "").strip()
        parser = spec.get("parser") if spec else None
        parsed = parser(raw_str) if parser else None
        value: Optional[object] = parsed if parsed is not None else (raw_str or None)
        if value is None:
            return
        base_key = spec.get("key") if spec and spec.get("key") else _slugify_key(label)
        key = _ensure_unique_key(base_key, seen_keys)
        fact: Dict[str, object] = {"key": key, "label": label, "value": value}
        if spec and "group" in spec:
            fact["group"] = spec["group"]
        if spec and "order" in spec:
            fact["order"] = spec["order"]
        if not spec:
            fact["order"] = _DEFAULT_FACT_ORDER + fallback_index
            fallback_index += 1
        facts.append(fact)
        if spec and spec.get("key"):
            derived_value: object = parsed if parsed is not None else value
            derived[spec["key"]] = derived_value

    for spec in _KEY_FACT_SPECS:
        include = spec.get("include")
        if not include:
            continue
        match = _pop_matching_attr(remaining, include, spec.get("exclude"))
        if not match:
            continue
        label, raw_value = match
        _add_fact(label, raw_value, spec)

    for label, raw_value in remaining.items():
        raw_str = str(raw_value or "").strip()
        if not raw_str:
            continue
        _add_fact(label, raw_str, None)

    if extras:
        for item in extras:
            value = item.get("value")
            if value is None:
                continue
            label = item.get("label") or item["key"]
            key = item["key"]
            existing = next((fact for fact in facts if fact["key"] == key), None)
            if existing:
                if existing.get("value") is None:
                    existing["value"] = value
                if "label" in item:
                    existing["label"] = label
                if "group" in item and item["group"]:
                    existing.setdefault("group", item["group"])
                if "order" in item and item["order"] is not None:
                    existing["order"] = item["order"]
            else:
                seen_keys.add(key)
                fact: Dict[str, object] = {"key": key, "label": label, "value": value}
                if "group" in item and item["group"]:
                    fact["group"] = item["group"]
                if "order" in item and item["order"] is not None:
                    fact["order"] = item["order"]
                else:
                    fact["order"] = _DEFAULT_FACT_ORDER + fallback_index
                    fallback_index += 1
                facts.append(fact)
            derived.setdefault(key, value)

    indexed = list(enumerate(facts))
    indexed.sort(
        key=lambda item: (
            cast(int, item[1].get("order", _DEFAULT_FACT_ORDER)),
            item[0],
        )
    )
    ordered_facts = [fact for _, fact in indexed]
    return ordered_facts, derived


def choose_area_m2(attrs: Dict[str, str], page_text: str) -> Optional[float]:
    bra_keys = ["bruksareal", "bra"]
    prom_keys = ["primærrom", "p-rom", "prom", "p rom"]
    area_keys = ["boligareal", "areal"]

    def _get_first(keys: List[str]) -> Optional[float]:
        for want in keys:
            for k, v in attrs.items():
                if _norm(want) in _norm(k):
                    val = _parse_m2_from_text(v)
                    if val:
                        return val
        return None

    v = _get_first(bra_keys) or _get_first(prom_keys) or _get_first(area_keys)
    if v:
        return v

    text = page_text or ""
    for kw in bra_keys + prom_keys + area_keys:
        rx = re.compile(
            rf"{re.escape(kw)}[^0-9]{{0,40}}(\d+(?:[.,]\d+)?)\s*(?:m²|m2|m\^2|kvm)",
            re.IGNORECASE,
        )
        m = rx.search(text)
        if m:
            return _to_float(m.group(1))

    return _parse_m2_from_text(text)


def choose_rooms(attrs: Dict[str, str], page_text: str) -> Optional[int]:
    for want in ["soverom", "antall soverom", "rom", "antall rom"]:
        for k, v in attrs.items():
            if _norm(want) in _norm(k):
                m = re.search(r"(\d+)", str(v))
                if m:
                    return int(m.group(1))
    m = re.search(r"(?:soverom|rom)\D{0,10}(\d+)", page_text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Scrape hovedinfo fra FINN (bilde, adresse, pris, areal, rom, geo)
# ──────────────────────────────────────────────────────────────────────────────
def _address_from_jsonld(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    addr: Any = item.get("address") or {}
    if isinstance(addr, list) and addr:
        addr = addr[0]
    if not isinstance(addr, dict):
        return None
    street = (addr.get("streetAddress") or "").strip()
    locality = (addr.get("addressLocality") or "").strip()
    postal = (addr.get("postalCode") or "").strip()
    if street and postal and locality:
        return f"{street}, {postal} {locality}"
    if street or locality:
        return (street or locality) or None
    return None


def _clean_address(s: str) -> str:
    s = re.sub(r"^\s*Kart\s+", "", s).strip()
    s = re.sub(r"\s+(Prisantydning|Totalpris)\s*$", "", s, flags=re.I).strip()
    return s


def _num(s: Any) -> Optional[int]:
    if s is None:
        return None
    t = re.sub(r"[^0-9,\.]", "", str(s)).replace(".", "").replace(",", ".")
    try:
        return int(round(float(t)))
    except Exception:
        return None


def scrape_finn(url: str) -> Dict[str, object]:
    """
    Skraper nøkkelinformasjon fra FINN-objektside: bilde, adresse, totalpris,
    evt. felleskost, areal (BRA/P-rom/boligareal), antall rom og lat/lon.
    """
    out: Dict[str, object] = {"source_url": url}
    try:
        sess = new_session()
        html_text = fetch_html(url, sess=sess)
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text(" ", strip=True)

        # bilde
        try:
            img: Optional[str] = None
            og = soup.find("meta", property="og:image")
            if isinstance(og, Tag):
                cand = og.get("content")
                if isinstance(cand, str) and cand:
                    img = cand
            if not img:
                tw = soup.find("meta", attrs={"name": "twitter:image"})
                if isinstance(tw, Tag):
                    cand = tw.get("content")
                    if isinstance(cand, str) and cand:
                        img = cand
            if not img:
                for tag in soup.find_all("script", type="application/ld+json"):
                    if not isinstance(tag, Tag):
                        continue
                    try:
                        blob = json.loads(tag.string or "{}")
                    except Exception:
                        continue
                    items = blob if isinstance(blob, list) else [blob]
                    for item in items:
                        if isinstance(item, dict):
                            if isinstance(item.get("image"), str) and not img:
                                img = cast(str, item["image"])
                            elif isinstance(item.get("image"), list) and item["image"]:
                                if isinstance(item["image"][0], str):
                                    img = item["image"][0]
                        if img:
                            break
                    if img:
                        break
            if not img and hasattr(soup, "select_one"):
                gimg = soup.select_one(
                    "img[data-testid='gallery-image'], img[src*='images']"
                )
                if isinstance(gimg, Tag):
                    src = gimg.get("src")
                    if isinstance(src, str) and src:
                        img = src
            if img:
                out["image"] = img
        except Exception:
            pass

        # adresse & pris (+ geo)
        found_addr: Optional[str] = None
        found_price: Optional[int] = None

        try:
            addr_tag = soup.select_one('[data-testid="object-address"]')
            if isinstance(addr_tag, Tag):
                cand = _clean_address(addr_tag.get_text(strip=True))
                if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                    found_addr = cand
        except Exception:
            pass

        try:
            lat_lon_set = False
            for tag in soup.find_all("script", type="application/ld+json"):
                if not isinstance(tag, Tag):
                    continue
                try:
                    blob = json.loads(tag.string or "{}")
                except Exception:
                    continue
                items: List[Any] = blob if isinstance(blob, list) else [blob]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if not found_addr:
                        a = _address_from_jsonld(item)
                        if a:
                            cand = _clean_address(a)
                            if any(ch.isdigit() for ch in cand) and len(cand) <= 80:
                                found_addr = cand

                    if not found_price:
                        offers: Any = item.get("offers") or {}
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict):
                            price = offers.get("price") or (
                                (offers.get("priceSpecification") or {})
                                if isinstance(offers.get("priceSpecification"), dict)
                                else {}
                            )
                            if isinstance(price, dict):
                                price = price.get("price")
                            if price is not None:
                                n = _num(price)
                                if n:
                                    found_price = n

                    if not lat_lon_set:
                        geo: Any = item.get("geo") or {}
                        if isinstance(geo, dict):
                            lat = geo.get("latitude")
                            lon = geo.get("longitude")
                            if lat is not None and lon is not None:
                                try:
                                    out["lat"] = float(str(lat).replace(",", "."))
                                    out["lon"] = float(str(lon).replace(",", "."))
                                    lat_lon_set = True
                                except Exception:
                                    pass
        except Exception:
            pass

        if found_addr:
            out["address"] = found_addr
        if found_price:
            out["total_price"] = found_price
            out.setdefault("totalpris", found_price)

        if "total_price" not in out:
            try:
                m = re.search(
                    r"(Totalpris)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?", text, flags=re.I
                )
                if m:
                    out["total_price"] = _num(m.group(2))
            except Exception:
                pass
        if "total_price" not in out:
            try:
                m = re.search(
                    r"(Prisantydning)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?", text, flags=re.I
                )
                if m:
                    out["total_price"] = _num(m.group(2))
            except Exception:
                pass

        if "hoa_month" not in out:
            try:
                m = re.search(
                    r"(Felleskostnader|Felleskost/mnd\.?|Fellesutgifter)\s*[:\s]\s*([0-9\s\.\u00A0]+)kr?",
                    text,
                    flags=re.I,
                )
                if m:
                    out["hoa_month"] = _num(m.group(2))
            except Exception:
                pass
        if "hoa_month" in out and "felleskostnader" not in out:
            out["felleskostnader"] = out["hoa_month"]

        if "total_price" in out and "totalpris" not in out:
            out["totalpris"] = out["total_price"]

        try:
            attrs = _collect_attrs(soup)
        except Exception:
            attrs = {}
        try:
            raw_facts = _extract_key_facts_raw(soup)
        except Exception:
            raw_facts = []
        if raw_facts:
            out["keyFactsRaw"] = raw_facts
            out["key_facts_raw"] = raw_facts
        try:
            extras: List[ExtraFact] = [
                {"key": "total_price", "label": "Totalpris", "value": out.get("total_price"), "group": "Kostnader", "order": 10},
                {"key": "hoa_month", "label": "Felleskostnader", "value": out.get("hoa_month"), "group": "Kostnader", "order": 40},
            ]
            key_facts, derived = _build_key_facts(attrs, extras=extras)
            if key_facts:
                out["keyFacts"] = key_facts
                out["key_facts"] = key_facts
            for k, v in derived.items():
                if v is None:
                    continue
                out.setdefault(k, v)
        except Exception:
            pass
        try:
            a = choose_area_m2(attrs, text)
            if a is not None:
                out["area_m2"] = float(a)
        except Exception:
            pass
        try:
            r = choose_rooms(attrs, text)
            if r is not None and "rooms" not in out:
                out["rooms"] = int(r)
        except Exception:
            pass

    except Exception:
        pass

    return out


_JSON_VALUE_KEYS = (
    "value",
    "amount",
    "amountvalue",
    "amount_value",
    "raw",
    "rawvalue",
    "raw_value",
    "number",
    "price",
    "pricevalue",
    "price_value",
    "formatted",
    "formattedvalue",
)

_ISO_DATETIME_RX = re.compile(r"\d{4}-\d{2}-\d{2}T?")


def _collect_json_blobs(soup: BeautifulSoup) -> List[Any]:
    blobs: List[Any] = []
    if hasattr(soup, "find_all"):
        for tag in soup.find_all("script", type="application/ld+json"):
            if not isinstance(tag, Tag):
                continue
            try:
                raw = tag.string or tag.get_text() or ""
            except Exception:
                raw = ""
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            blobs.append(data)
    try:
        tag = soup.find("script", id="__NEXT_DATA__")
        if isinstance(tag, Tag):
            raw = tag.string or tag.get_text() or ""
            if raw:
                data = json.loads(raw)
                blobs.append(data)
    except Exception:
        pass
    return blobs


def _pick_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, dict):
        for key in _JSON_VALUE_KEYS:
            if key in value:
                candidate = _pick_scalar(value[key])
                if candidate is not None:
                    return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _pick_scalar(item)
            if candidate is not None:
                return candidate
    return None


def _prepare_targets(items: Tuple[str, ...]) -> Tuple[Tuple[str, set[str]], ...]:
    targets: List[Tuple[str, set[str]]] = []
    for item in items:
        if not item:
            continue
        norm = _norm_compact(item)
        words = set(filter(None, _norm_tokens(item).split()))
        targets.append((norm, words))
    return tuple(targets)


def _json_key_matches(key_norm: str, key_words: set[str], target_norm: str, target_words: set[str]) -> bool:
    if not target_norm:
        return False
    if key_norm == target_norm or key_norm.startswith(target_norm):
        return True
    if target_norm and target_norm in key_norm:
        return True
    if target_words and target_words.issubset(key_words):
        return True
    return False


def _walk_json_for_keys(node: Any, targets: Tuple[Tuple[str, set[str]], ...]) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            key_norm = _norm_compact(key)
            key_words = set(filter(None, _norm_tokens(key).split()))
            for target_norm, target_words in targets:
                if _json_key_matches(key_norm, key_words, target_norm, target_words):
                    candidate = _pick_scalar(value)
                    if candidate is not None:
                        return candidate
            result = _walk_json_for_keys(value, targets)
            if result is not None:
                return result
    elif isinstance(node, list):
        for item in node:
            result = _walk_json_for_keys(item, targets)
            if result is not None:
                return result
    return None


def _find_in_json(ctx: _ExtractionContext, keys: Tuple[str, ...]) -> Any:
    targets = _prepare_targets(keys)
    if not targets:
        return None
    for blob in ctx.json_blobs:
        result = _walk_json_for_keys(blob, targets)
        if result is not None:
            return result
    return None


def _find_in_attrs(attrs: Dict[str, str], labels: Tuple[str, ...]) -> Any:
    if not attrs or not labels:
        return None
    targets = _prepare_targets(labels)
    if not targets:
        return None
    for key, value in attrs.items():
        key_norm = _norm_compact(key)
        if not key_norm:
            continue
        key_words = set(filter(None, _norm_tokens(key).split()))
        for target_norm, target_words in targets:
            if _json_key_matches(key_norm, key_words, target_norm, target_words):
                return value
    return None


def _json_address_from_blobs(blobs: List[Any]) -> Optional[str]:
    def _search(node: Any) -> Optional[str]:
        if isinstance(node, dict):
            street = None
            postal = None
            city = None
            for key, value in node.items():
                key_norm = _norm_compact(key)
                if key_norm in {"streetaddress", "addressline1", "address_line_1"}:
                    street = str(_pick_scalar(value) or value or "").strip()
                elif key_norm in {"postalcode", "postnumber", "zip"}:
                    postal = str(_pick_scalar(value) or value or "").strip()
                elif key_norm in {"addresslocality", "city", "town"}:
                    city = str(_pick_scalar(value) or value or "").strip()
            if street:
                tail = " ".join(part for part in (postal, city) if part)
                return ", ".join(part for part in (street, tail) if part).strip()
            for child in node.values():
                found = _search(child)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _search(item)
                if found:
                    return found
        return None

    for blob in blobs:
        found = _search(blob)
        if found:
            return found
    return None


def _json_showings_from_blobs(blobs: List[Any]) -> Optional[List[Any]]:
    collected: List[Any] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            key_norms = {_norm_compact(k) for k in node}
            has_showing_key = any("viewing" in kn or "showing" in kn for kn in key_norms)
            if has_showing_key:
                start = None
                end = None
                for key, value in node.items():
                    k_norm = _norm_compact(key)
                    candidate = _pick_scalar(value)
                    if isinstance(candidate, str) and not _ISO_DATETIME_RX.search(candidate):
                        candidate = None
                    if candidate is None:
                        continue
                    if any(token in k_norm for token in ("from", "start", "fra")):
                        start = candidate
                    elif any(token in k_norm for token in ("to", "end", "til", "slutt")):
                        end = candidate
                    elif start is None:
                        start = candidate
                if start or end:
                    collected.append({"start": start, "end": end})
            for child in node.values():
                _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for blob in blobs:
        _walk(blob)

    return collected or None


def _fallback_finnkode(ctx: _ExtractionContext) -> Optional[str]:
    try:
        parsed = urlparse(ctx.url)
        q = parse_qs(parsed.query)
        if q.get("finnkode"):
            return str(q["finnkode"][0])
    except Exception:
        pass
    try:
        m = re.search(r"(\d{6,})", ctx.url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _clean_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        value = _pick_scalar(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_currency(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    candidate = _pick_scalar(value)
    if isinstance(candidate, (int, float)):
        return int(round(float(candidate)))
    text = str(candidate if candidate is not None else value)
    text = text.replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"(?i)kr", "", text)
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _clean_integer(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    candidate = _pick_scalar(value)
    if isinstance(candidate, int):
        return candidate
    text = str(candidate if candidate is not None else value)
    text = text.replace("\u00a0", "")
    m = re.search(r"-?\d+", text)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def _clean_area(value: Any) -> Optional[float | int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    else:
        candidate = _pick_scalar(value)
        if isinstance(candidate, (int, float)):
            num = float(candidate)
        else:
            text = str(candidate if candidate is not None else value)
            text = text.replace("\u00a0", "").replace(" ", "")
            for token in ("m²", "m2", "kvm", "kvadratmeter"):
                text = text.replace(token, "")
            text = text.replace(",", ".")
            cleaned = re.sub(r"[^0-9.]", "", text)
            if not cleaned:
                return None
            if cleaned.count(".") > 1:
                parts = cleaned.split(".")
                cleaned = "".join(parts[:-1]) + "." + parts[-1]
            try:
                num = float(cleaned)
            except Exception:
                return None
    if abs(num - round(num)) < 1e-6:
        return int(round(num))
    return num


def _clean_showings(value: Any) -> Optional[List[str]]:
    if value is None:
        return None

    items: List[str] = []

    def _add(raw: Any) -> None:
        if raw is None:
            return
        text = str(raw).strip()
        if not text:
            return
        if "ingen" in _norm_tokens(text):
            return
        items.append(text)

    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                start = entry.get("start") or entry.get("from")
                end = entry.get("end") or entry.get("to")
                if isinstance(start, (list, dict)):
                    start = _pick_scalar(start)
                if isinstance(end, (list, dict)):
                    end = _pick_scalar(end)
                if start and end:
                    _add(f"{start}/{end}")
                elif start:
                    _add(start)
                elif end:
                    _add(end)
                else:
                    _add(_pick_scalar(entry))
            else:
                _add(entry)
    else:
        candidate = _pick_scalar(value)
        text = str(candidate if candidate is not None else value)
        parts = re.split(r"[\n,]+", text)
        for part in parts:
            _add(part)

    deduped: List[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped or None


FIELD_SPECS: Tuple[_FieldSpec, ...] = (
    _FieldSpec(
        name="adresse",
        json_getter=lambda ctx: _json_address_from_blobs(ctx.json_blobs),
        html_labels=("adresse",),
        cleaner=_clean_string,
    ),
    _FieldSpec(
        name="bydel",
        json_keys=("bydel", "district", "neighbourhood", "neighborhood", "område"),
        html_labels=("bydel", "område", "stadsdel"),
        cleaner=_clean_string,
    ),
    _FieldSpec(
        name="kommune",
        json_keys=("kommune", "municipality"),
        html_labels=("kommune",),
        cleaner=_clean_string,
    ),
    _FieldSpec(
        name="boligtype",
        json_keys=("boligtype", "propertytype", "objecttype"),
        html_labels=("boligtype", "type bolig"),
        cleaner=_clean_string,
    ),
    _FieldSpec(
        name="eieform",
        json_keys=("eieform", "ownership", "ownershiptype"),
        html_labels=("eieform", "eierform"),
        cleaner=_clean_string,
    ),
    _FieldSpec(
        name="prisantydning",
        json_keys=("prisantydning", "askingprice", "asking_price"),
        html_labels=("prisantydning", "pris"),
        cleaner=_clean_currency,
    ),
    _FieldSpec(
        name="totalpris",
        json_keys=("totalpris", "totalprice", "pris_total", "sumtotal"),
        html_labels=("totalpris",),
        cleaner=_clean_currency,
    ),
    _FieldSpec(
        name="fellesgjeld",
        json_keys=("fellesgjeld", "jointdebt"),
        html_labels=("fellesgjeld",),
        cleaner=_clean_currency,
    ),
    _FieldSpec(
        name="felleskostnader",
        json_keys=("felleskost", "felleskostnad", "felleskostnader", "sharedcost", "monthlycost"),
        html_labels=("felleskost", "felleskostnader", "fellesutgifter"),
        cleaner=_clean_currency,
    ),
    _FieldSpec(
        name="omkostninger",
        json_keys=("omkostninger", "kostnader", "costsum", "additionalcost"),
        html_labels=("omkostninger",),
        cleaner=_clean_currency,
    ),
    _FieldSpec(
        name="kommunale avgifter",
        json_keys=("kommunaleavgifter", "municipalfees"),
        html_labels=("kommunale avgifter", "kommunaleavgifter"),
        cleaner=_clean_currency,
    ),
    _FieldSpec(
        name="formuesverdi",
        json_keys=("formuesverdi", "taxvalue", "assessedvalue"),
        html_labels=("formuesverdi",),
        cleaner=_clean_currency,
    ),
    _FieldSpec(
        name="soverom",
        json_keys=("soverom", "antallsoverom", "bedrooms"),
        html_labels=("soverom", "antall soverom"),
        cleaner=_clean_integer,
    ),
    _FieldSpec(
        name="etasje",
        json_keys=("etasje", "floor", "floornumber"),
        html_labels=("etasje",),
        cleaner=_clean_integer,
    ),
    _FieldSpec(
        name="byggeår",
        json_keys=("byggeår", "byggår", "yearbuilt", "constructionyear"),
        html_labels=("byggeår", "byggår", "byggeaar"),
        cleaner=_clean_integer,
    ),
    _FieldSpec(
        name="energimerke",
        json_keys=("energimerke", "energimerking", "energylabel", "energyclass"),
        html_labels=("energimerke", "energimerking", "energi"),
        cleaner=_clean_string,
    ),
    _FieldSpec(
        name="primærrom (m²)",
        json_keys=("primærrom", "primaerrom", "p-rom", "prom", "primaryroom"),
        html_labels=("primærrom", "p-rom", "prom"),
        cleaner=_clean_area,
    ),
    _FieldSpec(
        name="BRA (m²)",
        json_keys=("bruksareal", "bra", "bra_totalt"),
        html_labels=("bruksareal", "bra", "bra totalt"),
        cleaner=_clean_area,
    ),
    _FieldSpec(
        name="tomt (m²)",
        json_keys=("tomteareal", "tomtestørrelse", "plotarea", "lotarea", "tomt"),
        html_labels=("tomteareal", "tomtestørrelse", "tomt"),
        cleaner=_clean_area,
    ),
    _FieldSpec(
        name="finn-kode",
        json_keys=("finnkode", "adid", "listingid", "finncode"),
        cleaner=_clean_string,
        fallback=_fallback_finnkode,
    ),
    _FieldSpec(
        name="visningstidspunkter",
        json_getter=lambda ctx: _json_showings_from_blobs(ctx.json_blobs),
        html_labels=("visning", "visninger"),
        cleaner=_clean_showings,
    ),
)


def scrape_finn_key_numbers(url: str) -> Dict[str, Any]:
    html_text = fetch_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    try:
        attrs = _collect_attrs(soup)
    except Exception:
        attrs = {}
    try:
        raw_facts = _extract_key_facts_raw(soup)
    except Exception:
        raw_facts = []
    for fact in raw_facts:
        label = fact.get("label")
        value = fact.get("value")
        if isinstance(label, str) and label not in attrs and value is not None:
            attrs[label] = str(value)

    json_blobs = _collect_json_blobs(soup)
    ctx = _ExtractionContext(url=url, soup=soup, attrs=attrs, json_blobs=json_blobs)

    results: Dict[str, Any] = {}
    for spec in FIELD_SPECS:
        value: Any = None
        if spec.json_getter:
            try:
                value = spec.json_getter(ctx)
            except Exception:
                value = None
        if value is None and spec.json_keys:
            try:
                value = _find_in_json(ctx, spec.json_keys)
            except Exception:
                value = None
        if value is None and spec.html_labels:
            try:
                value = _find_in_attrs(ctx.attrs, spec.html_labels)
            except Exception:
                value = None
        if value is None and spec.fallback:
            try:
                value = spec.fallback(ctx)
            except Exception:
                value = None
        if spec.cleaner:
            try:
                value = spec.cleaner(value)
            except Exception:
                value = None
        results[spec.name] = value if value not in ("", []) else None

    return results
