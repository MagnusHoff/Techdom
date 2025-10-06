# Struktur etter omstrukturering

- Bibliotekskoden ligger i `src/techdom/` og er delt inn etter ansvar: `domain`, `ingestion`, `processing`, `integrations`, `infrastructure` og `services`.
- Appene ligger i `apps/` der `frontend/` håndterer web-frontend og `api/` exponerer FastAPI-endepunkter.
- `bootstrap.py` sørger for at begge appene og alle CLI-verktøy finner `techdom`-pakkene uten manuell `sys.path`-håndtering.
- `scripts/` inneholder kun tynne wrappers som videresender til `techdom.cli.*`.
- `data/` er strukturert i `raw/`, `processed/`, `cache/`, `static/` og `debug/` for å skille rådata fra avledede filer.
- Dokumentasjon og migrasjonsnotater ligger i `docs/`.

Før refaktoren lå det meste under `core/` og direkte i rotmappen (Streamlit, API, skript). Den nye strukturen gir tydelige grenser mellom bibliotek, apper, data og dokumentasjon.
