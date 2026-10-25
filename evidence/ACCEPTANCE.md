# Acceptance record — 2026-10-25 mobile / AnoFox update

| Requirement | Evidence and status |
|---|---|
| Public HTTPS dashboard | Live at https://eleicoes2026.computadorpira.com ; mobile map and forecast tab rendered and inspected |
| GitHub repository | https://github.com/gustavoleo/eleicao-forecast-2026 |
| Parser, projection, arithmetic map, fixtures | 61 core tests pass in Linux, including the signed native AnoFox integration (`pytest-anofox-linux.txt`); six HTTP tests intentionally skipped there |
| HTTP failure handling | Six separate real HTTP/Postman integration tests pass (`pytest-http-latest.txt`): 403/429/500, HTML bodies, Retry-After, circuit-breaker suppression and conditional 304 |
| API contract | Updated EA12 and municipal EA20 contract; four requests and four assertions, zero failures (`postman-municipal.txt`) |
| Rate / cache | 314.55 s; 1,836 requests; 117 HTTP 304; peak 11 completed responses/s, mean 5.837/s (`rate-municipal.json`). All measured traffic below 90/s |
| Live source | Public evidence captured at 19:13 BRT; source 19:12:36, 1.690743% counted, no active upstream errors (`deploy-mobile-anofox.json`). This snapshot is historical, not the present tally |
| Primary forecast | Signed anofox_forecast v2026.10.22 installed and exercised: Naive, AutoETS, AutoARIMA, Theta, chronological holdouts, coherent scenarios. Headline withheld while count-progress history is insufficient |
| Monte Carlo / pairs | Tests cover coherent shares, fixed counted votes, ranked first-round pairs and second-round not-applicable semantics; no model establishes mathematical certainty |
| Historical data | Official 2022 archive: 364 UF and 74,763 municipal candidate aggregates. Municipal sums reconcile exactly to UF aggregates. Final 2026 first-round priors collected by corresponding territory |
| Municipal refinement | Implemented and running; 5,757 scheduled current-round locations. Initial sweep still progressing. Only complete, fresh groups reconciled with parent totals replace UFs; binary section files not decoded |
| Persistence | Named volume retained and database backed up while stopped before updates. Restart restored municipal and first-round sources with original timestamps, then continued collection |
| Mathematical state colors | All 27 UF boundaries from IBGE. Color only for a fresh, complete conservative local lead proof. Unresolved/stale/missing data remain gray. This does not declare the national president |
| First-round comparison | Exact same-person vote totals and percentage-point differences shown. Same first-round leader marker is separate from exact vote equality. Final first round versus partial second round is labeled |
| Mobile / auto-refresh | 390 px rendered map inspected with no page overflow; native 60-second refresh verified. Data-source time remains separate from collector checks |
| Hardcharger | DuckDB fallback verified. Remote Hardcharger was not supplied; remote end-to-end storage remains unverified |
| Deployment fallback | Existing Podman + dedicated Cloudflare Tunnel works; host must stay on. Streamlit Cloud required sign-in/terms; Render/HF alternatives documented |
| Secrets | Targeted credential-pattern scan and ignore checks performed before commit; not a proof that every arbitrary string is non-secret |
| National elected candidate | **NOT ACHIEVED** at the recorded snapshot. No candidate declared elected; collection and independent gate continue |

Older evidence files remain as dated records of earlier deployments. They must not be read as the latest model version, latest tally, or current CI status. The active goal remains open; an existing five-minute thread heartbeat monitors service/result status while the container collects independently.
