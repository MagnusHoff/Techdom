# Data mappeoppsett

- `data/raw/`
  - `proxy/`: inngående proxy-lister og resultatlogg.
  - `ssb_query_09895.json`: original spørring mot SSB API.
- `data/processed/`
  - `rent_m2.csv`: ferdig beregnet leie per m² som brukes i UI/API.
- `data/cache/`
  - `prospekt/`: nedlastede prospekt PDF-er/metadata.
  - `analysis_history.jsonl`: historikk over analyser som vises i UI.
  - `rate_cache.json`: mellomlagring av renteoppslag.
- `data/static/`
  - `geo/`: geojson for bydeler.
  - `lookup/postnr/`: postnummeroppslag (oslo/bergen).
- `data/debug/`
  - `failcases/`: feilsituasjoner med rå JSON/PDF for videre analyse.

Alle nye datasett bør legges i riktig nivå slik at backup/utrulling kan håndtere rådata, prosesserte data og caches separat.
