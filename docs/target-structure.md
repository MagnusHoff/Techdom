# Ny prosjektstruktur

- `bootstrap.py`: Holder rot og `src/` på `sys.path` slik at alle verktøy finner `techdom`-pakkene.
- `src/techdom/`
  - `domain/`: datamodeller, kontrakter og historikk.
  - `ingestion/`: all scraping, driver-moduler, sesjoner og HTTP-hjelpere.
  - `processing/`: analyser, PDF-/AI-verktøy og leieberegning.
  - `integrations/`: S3, SSB og andre eksterne tjenester.
  - `infrastructure/`: konfigurasjon, telleverk og felles infrastruktur.
  - `services/`: applikasjonsnære tjenester (jobbkøer m.m.).
  - `cli/`, `web/`: plassholdere for kommandolinje-verktøy og delte webkomponenter.
- `apps/`
  - `streamlit/`: Streamlit-app med `main.py`, visninger under `views/`, og hjelpetjenester under `services/`.
  - `api/`: FastAPI-app i `main.py` – gjenbruker tjenester fra `techdom.services`.
- `app.py`: tynn wrapper som lar `streamlit run app.py` starte `apps.streamlit.main`.
- `api/app.py`: wrapper for `uvicorn` som re-eksporterer `apps.api.main`.
- `scripts/`: legacy-inngangspunkt som bare videresender til `techdom.cli.*`. Nye verktøy legges direkte under `src/techdom/cli/`.
- `data/`: delt inn i `raw/`, `processed/`, `cache/`, `static/` og `debug/`.
- `docs/`: arkitektur- og migrasjonsnotater.

Aliaset `core.*` er fjernet; alle referanser skal gå direkte mot `techdom.*`-pakkene.
