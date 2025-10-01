# Tidligere struktur (før refaktor)

- `app.py`: Streamlit-app direkte i rotkatalogen, med manuell `sys.path`-håndtering og relative ressurser.
- `core/`: samlet nesten all domenelogikk, scraping, konfig og integrasjoner i ett stort modultre.
- `ui/`: Streamlit-visninger i egen mappe, men importerte alt fra `core`.
- `api/app.py`: FastAPI-app med imports fra `core`.
- `scripts/`: Engangsskript som også hentet funksjoner fra `core`.
- `data/`: Blandede statiske filer, genererte caches og debug-utdata i samme mappe.

Refaktoren flytter dette inn i dedikerte pakker og applikasjonsmapper for å få tydelig separasjon mellom bibliotekskode, apper og data.
