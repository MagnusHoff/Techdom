# Techdom Frontend (Next.js App Router)

Denne mappen inneholder den nye SaaS-frontenden for Techdom.ai. Prosjektet er
satt opp med Next.js 14, TypeScript og App Router.

## Kom i gang

```bash
cd apps/frontend
npm install
npm run dev
```

Legg til en lokal `.env` basert på `.env.example` med URL til FastAPI-backenden:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

For Next.js sitt build-system: bruk `.env.development.local` (eller sett variabelen i shell) for lokal utvikling. Ikke lag en `.env.local` i repoet – den lastes også i produksjonsbuilds og kan overstyre `NEXT_PUBLIC_API_BASE_URL` slik at prod peker mot `http://127.0.0.1:8000` igjen.

## Scripts

- `npm run dev` – starter utviklingsserveren på `http://localhost:3000`
- `npm run build` – bygger produksjonsbundle (`.next`)
- `npm run start` – kjører produksjonsserver (`next start`)
- `npm run lint` – Next.js/ESLint-regler
- `npm run typecheck` – TypeScript uten emit
- `npm run check` – kjører lint + typecheck (kan brukes i CI)
- `npm test` – alias for `npm run lint` inntil vi legger til egne tester

## Midlertidige begrensninger

- Automatisk henting av FINN-data er ikke på plass ennå; analyser-siden krever
  manuell input av tallene. Når scraping/API-utvidelsen er klar kan vi koble
  dette opp og gjøre feltene read-only med forhåndsutfylling.
- Ingen komponentbibliotek eller design tokens er lagt inn – vi bruker lett CSS
  for å få en Techdom-lignende estetikk inntil designteamet bestemmer videre.
