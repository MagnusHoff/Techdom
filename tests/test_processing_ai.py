import sys
import types

import pytest


class _StubOpenAI:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("OpenAI client should not be initialised in offline tests")


sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=_StubOpenAI))

from techdom.processing.ai import analyze_prospectus


@pytest.fixture(autouse=True)
def _clear_openai_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_analyze_prospectus_formats_component_and_reason() -> None:
    text = """
    TG 3 Bad: Store fuktskader i membran og sluk, må utbedres umiddelbart.
    TG2 Tak: Taktekking med mose og oppsprukne takstein, behov for utskifting snart.
    """
    result = analyze_prospectus(text)

    assert result["tg3"] == [
        "Bad: Store fuktskader i membran og sluk må utbedres umiddelbart."
    ]
    assert result["tg2"] == [
        "Tak: Taktekking med mose og oppsprukne takstein behov for utskifting snart."
    ]


def test_analyze_prospectus_caps_tg_lists_to_eight_items() -> None:
    components = [
        "Bad",
        "Tak",
        "Vinduer",
        "Yttervegger",
        "Ventilasjon",
        "Bereder",
        "Drenering",
        "Pipe",
        "Radon",
        "Takrenne",
    ]
    tg2_lines = "\n".join(
        f"TG2 {component}: Tiltak må vurderes snarlig på grunn av slitasje."
        for component in components
    )
    text = f"""
    TG 3 Våtromsmembran: Sviktende tetting rundt sluk registrert.
    {tg2_lines}
    """
    result = analyze_prospectus(text)

    tg2_items = result["tg2"]
    assert len(tg2_items) == 5
    for item in tg2_items:
        words = item.split()
        assert 8 <= len(words) <= 24
        assert ":" in item
        assert item.endswith(".")
        assert item.count(".") == 1


def test_analyze_prospectus_skips_non_issue_text() -> None:
    text = """
    TG2 Bad: Oppgradert bad med moderne fliser og ny belysning.
    TG2 Bad: Fuktmerker registrert rundt sluk og i hjørner.
    """
    result = analyze_prospectus(text)

    tg2_items = result["tg2"]
    assert len(tg2_items) == 1
    assert tg2_items[0].startswith("Bad: Fuktmerker registrert")
