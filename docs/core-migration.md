# Utfasing av `core`-aliaset

`core/__init__.py` er fjernet. Alle tidligere alias-importer må nå peke direkte på modulene i `techdom`-pakken. Denne notatfilen oppsummerer hva som ble gjort og hvordan eksterne prosjekter bør følge opp.

## Hva betyr dette?
- Eldre imports som `from core import rent` eller `import core.analysis_contracts` feiler nå med `ImportError`.
- Alle interne moduler er allerede flyttet til `techdom.*` og oppdatert til å bruke de nye stiene.
- Driver-registreringen bruker kun `techdom.ingestion.drivers.*` og har ikke lenger fallback til `core`.

## Sjekkliste for eksterne repoer
1. Søk etter `core.`-referanser og oppdater til tilsvarende `techdom.`-moduler.
2. Publiser en kort notis i relevante kanaler (README/CHANGELOG/Slack) om at `core`-aliaset er fjernet.
3. Stram inn lint-regler slik at nye `core.*`-imports blir behandlet som feil.

Når alle konsumenter er oppdatert trenger man ikke gjøre noe mer – gamle alias er helt borte, og nye moduler ligger kun under `techdom`-navnerommet.
