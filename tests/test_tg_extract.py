from __future__ import annotations

import json
from pathlib import Path

from techdom.processing.tg_extract import (
    coerce_tg_strings,
    extract_tg,
    format_tg_entries,
    merge_tg_lists,
    summarize_tg_entries,
    summarize_tg_strings,
)


def test_extract_tg_from_html(tmp_path: Path) -> None:
    html = """
    <html>
      <body>
        <p>TG 3 Bad: Store skader i membran og tettsjikt</p>
        <p>Taktekking TG2 - Slitasje</p>
      </body>
    </html>
    """
    source_path = tmp_path / "prospekt.html"
    source_path.write_text(html, encoding="utf-8")

    result = extract_tg(str(source_path))
    markdown = result["markdown"]
    payload = result["json"]

    expected_markdown = (
        "TG2\n"
        "Taktekking - Slitasje.\n"
        "\n"
        "TG3\n"
        "Bad: Store skader i membran og tettsjikt."
    )
    assert markdown == expected_markdown

    assert payload["TG3"][0]["komponent"] == "Bad"
    assert payload["TG3"][0]["grunn"] == "Bad: Store skader i membran og tettsjikt."
    assert payload["TG2"][0]["komponent"] == "Tak"
    assert payload["TG2"][0]["grunn"] == "Taktekking - Slitasje."
    assert "Bad" not in payload["missing"]
    assert "Tak" not in payload["missing"]


def test_only_markdown_optional(tmp_path: Path) -> None:
    html = "<p>Ingen TG her</p>"
    path = tmp_path / "empty.html"
    path.write_text(html, encoding="utf-8")

    result = extract_tg(str(path))

    assert result["json"]["TG3"] == []
    assert result["json"]["TG2"] == []
    # ensure JSON serialises without errors
    json.dumps(result["json"], ensure_ascii=False)
    assert result["markdown"] == "TG2\nIngen TG2-punkter funnet.\n\nTG3\nIngen TG3-punkter funnet."


def test_extract_tg_filters_headers_and_cosmetics(tmp_path: Path) -> None:
    html = """
    <html>
      <body>
        <p>TG 2 Avvik som kan kreve tiltak</p>
        <p>Det er registrert fukt i kjellervegg.</p>
        <p>Tiltak: utbedring anbefales.</p>
        <p>TG 2 Kjøkken: Normal slitasje på fronter.</p>
        <p>TG 3 Store eller alvorlige avvik</p>
        <p>Lekkasje i takmembran observert.</p>
        <p>Konsekvens: risiko for følgeskader.</p>
      </body>
    </html>
    """
    path = tmp_path / "mix.html"
    path.write_text(html, encoding="utf-8")

    result = extract_tg(str(path))
    markdown = result["markdown"].splitlines()
    payload = result["json"]

    assert payload["TG2"][0]["grunn"] == "Det er registrert fukt i kjellervegg."
    assert payload["TG3"][0]["grunn"] == "Lekkasje i takmembran observert."
    assert len(payload["TG2"]) == 1
    assert len(payload["TG3"]) == 1
    assert any("fukt i kjellervegg" in line.lower() for line in markdown)
    assert not any("kjøkken" in item["grunn"].lower() for item in payload["TG2"])


def test_format_tg_entries_adds_prefix_and_source() -> None:
    entries = [
        {"komponent": "Tak", "grunn": "Taktekking - Slitasje.", "kilde_side": "10"}
    ]
    formatted = format_tg_entries(entries, level=2)
    assert formatted == ["TG2 Tak: Taktekking - Slitasje. (Tilstandsrapport side 10)"]


def test_merge_tg_lists_preserves_order_and_deduplicates() -> None:
    merged = merge_tg_lists(
        ["TG2 Tak: Funn.", "TG2 Bad: Funn."],
        ["TG2 Bad: Funn.", "TG2 Kjøkken: Funn."],
        limit=None,
    )
    assert merged == ["TG2 Tak: Funn.", "TG2 Bad: Funn.", "TG2 Kjøkken: Funn."]


def test_coerce_tg_strings_handles_scalars() -> None:
    assert coerce_tg_strings("TG2 Tak") == ["TG2 Tak"]
    assert coerce_tg_strings([" TG2 Tak ", "", None]) == ["TG2 Tak"]


def test_summarize_tg_entries_builds_label_and_detail() -> None:
    entries = [
        {"komponent": "Taktekking", "grunn": "Mer enn halvparten av brukstid er passert.", "kilde_side": "5"}
    ]
    summary = summarize_tg_entries(entries, level=2)
    assert summary
    assert "Taktekking" in summary[0]["label"]
    assert "brukstid" in summary[0]["label"].lower()
    assert "TG2" in summary[0]["detail"]
    assert summary[0]["detail"].endswith("(Tilstandsrapport side 5)")


def test_summarize_tg_strings_parses_freetext() -> None:
    summary = summarize_tg_strings(["TG3 Vinduer: Fukt registrert i karmene."], level=3)
    assert summary
    assert "Vinduer" in summary[0]["label"]
    assert summary[0]["detail"].startswith("TG3 Vinduer")
