# Utfasing av `core`-aliaset

`core/__init__.py` eksponerer fortsatt "gamle" importbaner ved å peke videre til moduler i `techdom`-pakken. Planen for å fjerne dette laget uten å brekke noe ser slik ut:

1. **Kartlegg eksterne avhengigheter**
   - Søk i andre repoer/prosjekter etter `from core` / `core.`-imports.
   - Lag issues/PR-er der for å migrere til `techdom.*`.
2. **Oppdater interne referanser**
   - Sørg for at nye moduler/tests aldri importerer via `core`.
   - Stram inn lint-regler (f.eks. Ruff/Flake8) med forbud mot `core.*`.
3. **Kommuniser utfasingstidspunkt**
   - Sett en dato (f.eks. etter to sprint-er) hvor aliaset fjernes.
   - Dokumenter i `CHANGELOG`/README at `core` er deprecated.
4. **Fjern aliaset**
   - Slett `core/__init__.py` og enhvert gammelt skall.
   - Kjør full regresjon + informer konsumenter om endringen.

Inntil alle eksterne brukssteder er oppdatert bør `core` ligge igjen for kompatibilitet, men denne planen gjør det enkelt å rydde bort senere.
