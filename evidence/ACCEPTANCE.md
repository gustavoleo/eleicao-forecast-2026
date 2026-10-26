# Acceptance record — 2026-10-26 mathematical call and scenario update

| Requirement | Evidence and status |
|---|---|
| Public HTTPS dashboard | Live at https://eleicoes2026.computadorpira.com ; mobile map and forecast tab rendered and inspected |
| GitHub repository | https://github.com/gustavoleo/eleicao-forecast-2026 |
| Parser, projection, arithmetic map, fixtures | 66 core tests pass in Linux, including the signed native AnoFox integration (`pytest-scenarios.txt`); six HTTP tests intentionally skipped there |
| HTTP failure handling | Six separate real HTTP/Postman integration tests pass (`pytest-http-latest.txt`): 403/429/500, HTML bodies, Retry-After, circuit-breaker suppression and conditional 304 |
| API contract | Updated EA12 and municipal EA20 contract; four requests and four assertions, zero failures (`postman-municipal.txt`) |
| Rate / cache | 314.55 s; 1,836 requests; 117 HTTP 304; peak 11 completed responses/s, mean 5.837/s (`rate-municipal.json`). All measured traffic below 90/s |
| Live source | Public evidence captured at 19:13 BRT; source 19:12:36, 1.690743% counted, no active upstream errors (`deploy-mobile-anofox.json`). This snapshot is historical, not the present tally |
| Primary forecast and scenarios | Signed anofox_forecast v2026.10.22; chronological validation passed on 240 observations / 99 count-progress grid points; 5,000 scenarios with lower, central and upper 90% share scenarios displayed (`current-mathematical-call.json`) |
| Monte Carlo / pairs | Tests cover coherent shares, fixed counted votes, ranked first-round pairs and second-round not-applicable semantics; no model establishes mathematical certainty |
| Historical data | Official 2022 archive: 364 UF and 74,763 municipal candidate aggregates. Municipal sums reconcile exactly to UF aggregates. Final 2026 first-round priors collected by corresponding territory |
| Municipal refinement | All 5,757 current-round locations were collected; only complete, fresh groups reconciled with parent totals replace UFs. Binary section files not decoded |
| Persistence | Named volume retained and database backed up while stopped before updates. Restart restored municipal and first-round sources with original timestamps, then continued collection |
| Mathematical state colors | All 27 UF boundaries from IBGE. Color only for a fresh, complete conservative local lead proof. Unresolved/stale/missing data remain gray. This does not declare the national president |
| First-round comparison | Exact same-person vote totals and percentage-point differences shown. Same first-round leader marker is separate from exact vote equality. Final first round versus partial second round is labeled |
| Mobile / auto-refresh | 390 px rendered map inspected with no page overflow; native 60-second refresh verified. Data-source time remains separate from collector checks |
| Hardcharger | DuckDB fallback verified. Remote Hardcharger was not supplied; remote end-to-end storage remains unverified |
| Deployment fallback | Existing Podman + dedicated Cloudflare Tunnel works; host must stay on. Streamlit Cloud required sign-in/terms; Render/HF alternatives documented |
| Secrets | Targeted credential-pattern scan and ignore checks performed before commit; not a proof that every arbitrary string is non-secret |
| Mathematical result | **ACHIEVED for the verified published counts:** direct current official TSE round-2 EA20 (`md="e"`) and independent arithmetic gate agree on candidate 13. 64,111,352 > 59,749,523 + 305,566; TSE final totalization/candidate elected flag is still pending. No claim of final TSE proclamation. Direct file hash, matching vote totals and checked timestamp are in `current-mathematical-call.json` |

Older evidence files remain as dated records of earlier deployments. They must not be read as the latest model version, tally, or CI status. The deployed collector continues checking the official source every 15 seconds for Brazil and every 60 seconds for UFs, so corrections can revise a nonfinal published-count call. A five-minute service/result heartbeat remains active.

The verification snapshot is 2026-10-26 00:20 UTC. It confirms the source is official and fresh, source/dashboard candidate vote totals match, no collection errors are present, the independent gate is mathematically secured, and health is true.
