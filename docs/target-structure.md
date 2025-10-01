# Ny prosjektstruktur

- `bootstrap.py`: Sørger for at både rot og `src/` ligger på `sys.path` for alle scripts/tests.
- `src/techdom/`
  - `ingestion/`: scraping, drivere, sessions, HTTP-headere.
  - `processing/`: analyser, renteberegninger, PDF/AI-hjelpere.
  - `integrations/`: S3, SSB og andre eksterne tjenester.
  - `domain/`: datakontrakter, historikk, geologikk.
  - `infrastructure/`: konfig og tverrgående tjenester.
  - `cli/`, `web/`: plassert for fremtidige CLI/verktøy og delte webkomponenter.
- `core/`: inneholder kun et kompatibilitetslag som videresender gamle importbaner til `techdom`.
- `apps/`
  - `streamlit/`: ny Streamlit-app med `main.py` og visninger under `views/`.
  - `api/`: FastAPI-app i `main.py`, re-eksportert via `api/app.py` for bakoverkompatibilitet.
- `app.py`: tynn wrapper som importerer og kjører `apps.streamlit.main` for Streamlit.
- `api/app.py`: wrapper som re-eksporterer `apps.api.main` for uvicorn.
- `data/`: delt inn i `raw/`, `processed/`, `cache/`, `static/geo`, `debug/` (eksisterende filer flyttes gradvis).
- `docs/`: arkitektur- og migrasjonsnotater.

Alle scripts og tester importerer `bootstrap` slik at `techdom`-pakken alltid er tilgjengelig uten ekstra miljøvariabler.
