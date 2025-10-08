from __future__ import annotations

import json
from pathlib import Path

from techdom.processing.tg_extract import extract_tg


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

    assert "TG3 (alvorlig):" in markdown
    assert "Bad – store skader i membran og tettsjikt" in markdown
    assert "TG2 (middels):" in markdown
    assert "Tak – slitasje registrert" in markdown

    assert payload["TG3"][0]["komponent"] == "Bad"
    assert payload["TG3"][0]["grunn"] == "store skader i membran og tettsjikt"
    assert payload["TG2"][0]["komponent"] == "Tak"
    assert payload["TG2"][0]["grunn"] == "slitasje registrert"
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
