# Techdom
AI-drevet eiendomskalkulator
Hei

## Prosjektstruktur
- `src/techdom/`: domenelogikk, integrasjoner og databehandling
- `apps/frontend/`: Next.js-applikasjonen som leverer den nye web-frontend-en
- `apps/api/`: FastAPI-app definert i `main.py` (re-eksporteres via `api/app.py`)
- `scripts/`: operasjonsskript som bruker `bootstrap` for å få `src/` på PYTHONPATH
- `data/`: delt mellom `raw/`, `processed/`, `cache/`, `static/` og `debug/` (se docs for detaljer)
- `docs/`: arkitektur og migrasjonsnotater

## Stripe-konfigurasjon
Miljøvariabler som brukes av abonnementstjenestene:
- `STRIPE_API_KEY`
- `STRIPE_PRICE_ID_MONTHLY`
- `STRIPE_PRICE_ID_YEARLY`
- `STRIPE_SUCCESS_URL` (valgfri)
- `STRIPE_CANCEL_URL` (valgfri)
- `STRIPE_PORTAL_RETURN_URL` (valgfri)
- `STRIPE_PORTAL_CONFIGURATION_ID` (valgfri, bruk når Stripe-kontoen din har flere portal-konfigurasjoner eller ingen standard)
- `STRIPE_WEBHOOK_SECRET`
