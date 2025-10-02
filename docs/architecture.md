# Arkitektur-oversikt

| Område | Sti | Ansvar |
| ------ | ---- | ------- |
| Domene | `src/techdom/domain/` | Datamodeller, kontrakter, historikk og geodata. |
| Ingest | `src/techdom/ingestion/` | Oppdage, laste ned og normalisere prospekter inkl. drivere. |
| Processing | `src/techdom/processing/` | Analyse- og beregningslogikk (AI, PDF, leie). |
| Integrasjoner | `src/techdom/integrations/` | Tilkoblinger mot eksterne tjenester (SSB, S3 osv.). |
| Infrastruktur | `src/techdom/infrastructure/` | Konfig, telleverk og andre tverrgående komponenter. |
| Tjenester | `src/techdom/services/` | Applikasjonsnære tjenester (jobbkøer, orchestrering). |
| CLI | `src/techdom/cli/` | Gjenbrukbare kommandolinjeverktøy. Kjør eksempelvis `PYTHONPATH=src python -m techdom.cli.build_rent_csv`. |
| Apper | `apps/` | Frontend (`streamlit`) og API (`api`) som bruker tjenestene over. |
| Data | `data/` | Rådata, bearbeidede datasett, cache og debug-filer. |
| Dokumentasjon | `docs/` | Arkitektur, migrasjoner og referanseinformasjon. |

For nye bidrag: plasser ren forretningslogikk i `src/techdom/`, la apper og skript være tynne lag som bare orkestrerer eksisterende tjenester.
