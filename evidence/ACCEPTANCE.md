# Acceptance record — 2026-10-25

| Requirement | Evidence and status |
|---|---|
| Public HTTPS dashboard | Live at https://eleicoes2026.computadorpira.com ; public Streamlit page inspected in browser, charts/source/warnings present |
| GitHub repository | https://github.com/gustavoleo/eleicao-forecast-2026 ; code and prompt pushed |
| Parser, projection and fixture dry run | `pytest.txt`: 32 passing tests with the real HTTP/Postman integration enabled |
| HTTP failure handling | Real requests through local Postman simulation: 403/429/500, HTML error bodies, Retry-After recovery, no additional request during origin cooldown |
| API contract | OpenAPI syntax lint clean; collection lint clean; two requests and two assertions passed (`postman-collection.txt`) |
| Hash cache and five-minute rate log | 318.19 s; 191 requests; 161 HTTP 304; peak three completed responses/s (`rate-compliance.json`, `live-five-minute-excerpt.txt`) |
| Live dry run / current leader | Direct TSE EA20 fetch and all 28 UFs succeeded. Model leader matches the leader in the obtained snapshot. Source generation was stale, so no claim of an up-to-the-minute official leader |
| 5,000 Monte Carlo draws / pairings | Numerical tests cover probability sums, ranked first-round pairs, CIs, fixed counted votes and second-round not-applicable semantics |
| Historical 2022 UF data | Official open-data ZIP presidential member downloaded, CRC/SHA-256 checked; 364 aggregate rows in persistent database |
| Durable state / restart | All five tables found in stopped-container backup; 20 stored projection rows retained and extended to 22 after deployment restart (`persistence-backup.json`, before/after states) |
| Hardcharger / fallback | Installed SQLFrame/ADBC dependencies and verified DuckDB deployment; no remote Hardcharger server supplied, remote integration unverified |
| No secrets committed | Credential-pattern `git grep` found no values; `.env`, secret token and data confirmed ignored. This is a targeted scan, not a mathematical proof that arbitrary strings contain no secret |
| Auto-refresh | Component path timed out publicly; native Streamlit timer deployed. With no reload or click, public DOM advanced from panel 18:25:07 BRT / model 18:24:36 to panel 18:26:03 / model 18:25:37. No component iframe or current component error remained |
| GitHub CI | Code commit ee9dc4ba741e488d991061bbf48c5d2a1aacf6f0 passed run 40235336563. CI covers 26 core tests; the six HTTP tests have separate local Postman evidence |
| Mobile / dark mode | Public page inspected in dark mode; mobile DOM at 390 px had scrollWidth=390 (no horizontal overflow) |
| Streamlit Cloud primary | Sign-in/terms gate encountered. Working Cloudflare/Podman fallback deployed; Render manifest and alternative instructions supplied |
| Mathematical or official elected candidate | **NOT ACHIEVED**. National TSE source stale; independent gate remains undecided. No candidate announced elected |
| Municipal/section refinement | **Not implemented**; current model is UF-level. Live EA12, municipal EA20 and Acre EA16 probes succeeded (`municipal-availability.json`); conditional finer-resolution requirement remains outstanding |
| Geographic reconciliation / future timestamps | Model v2 deployed; 33 core tests pass (`pytest-v4.txt`), including missing UFs, lagging UF totals, overlapping electorate, future timestamps, failed model recovery. Six unchanged HTTP integration tests skipped in this run; prior separate evidence remains valid |

The independent collector continues every minute (Brazil every 15 seconds). A thread heartbeat named `Election dashboard health and result` checks service/result state every five minutes. Local machine availability remains a dependency. The active goal has not been marked complete.
