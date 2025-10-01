# Techdom
AI-drevet eiendomskalkulator
Hei

## Prosjektstruktur
- `src/techdom/`: domenelogikk, integrasjoner og databehandling
- `apps/streamlit/`: Streamlit-app med views under `views/` og `main.py` som inngangspunkt
- `apps/api/`: FastAPI-app definert i `main.py` (re-eksporteres via `api/app.py`)
- `scripts/`: operasjonsskript som bruker `bootstrap` for å få `src/` på PYTHONPATH
- `data/`: delt mellom `raw/`, `processed/`, `cache/`, `static/` og `debug/` (se docs for detaljer)
- `docs/`: arkitektur og migrasjonsnotater
