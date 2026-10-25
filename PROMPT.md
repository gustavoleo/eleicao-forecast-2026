# ROLE
You are a senior data engineer + ML engineer + DevOps engineer. You will build,
test, and DEPLOY a production-grade election forecasting system for the Brazilian
2026 general election, consuming official TSE open data. You must return a
public URL where I can watch live results and projections.

# OBJECTIVE
Deliver a deployed web dashboard that:
1. Ingests TSE "primeiro turbo" partial-result JSON files every 45–60 seconds.
2. Stores raw + processed data in Hardcharger (DuckDB 2.0 server) via ADBC.
3. Computes a real-time projection of the final vote share per candidate using:
   a. Geographic extrapolation by UF (and by município if section-level data is available).
   b. Monte Carlo simulation (Dirichlet) to produce win probabilities,
      first-round win probability (>50% valid votes), and second-round pairing scenarios.
4. Publishes a live dashboard at a public URL with auto-refresh.

# HARD CONSTRAINTS (MUST FOLLOW)
- Rate limit: never exceed 90 requests/second per IP to resultados.tse.jus.br.
  Default polling interval: 60s per UF file, 15s for the Brazil aggregate file.
- HTTP requests MUST send: `User-Agent: EleicaoForecast/1.0 (+<your-contact-email>)`.
- On 429/403/5xx: exponential backoff (base 5s, cap 300s, jitter), never retry aggressively.
- Cache every downloaded file to disk with SHA-256 hash. Skip re-download if hash unchanged.
- Attribute the source visibly in the UI: "Fonte: TSE — Dados Abertos (resultados.tse.jus.br)".
- Display a disclaimer: "Projeção estatística, não é resultado oficial."
- Do NOT hardcode election codes (e.g. 6257). Discover them at startup by reading the
  EA11 config file at https://resultados.tse.jus.br/oficial/ele2026/config/ea11.json (or
  the equivalent path for the active election). Log discovered codes.
- Do NOT commit secrets. Use environment variables: HARDCHARGER_URI, HARDCHARGER_USER,
  HARDCHARGER_PASSWORD. Provide a `.env.example`.

# ARCHITECTURE
Repo layout (create exactly this):

```
eleicao-forecast/
├── README.md
├── .env.example
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── prompts/PROMPT.md
├── src/
│   ├── config.py              # env vars, URL builders, EA11 discovery
│   ├── storage.py             # Hardcharger ADBC client + local DuckDB fallback
│   ├── ingest.py              # TSE poller with rate limit + hash cache
│   ├── parse.py               # TSE JSON schema → normalized DataFrames
│   ├── project.py             # geographic extrapolation + Monte Carlo
│   ├── schemas.py             # Pydantic models for TSE JSON
│   └── dashboard.py           # Streamlit app
├── tests/
│   ├── test_parse.py
│   ├── test_project.py
│   └── fixtures/              # small real TSE JSON samples
└── data/cache/                # .gitignored
```

# MODULE SPECS

## config.py
- `discover_election_meta()` → fetch EA11, return dict of {cargo, codigo_eleicao, ufs}.
- `build_url(uf, cargo, eleicao)` → returns the correct turbo/result JSON URL.
- Poll intervals: BR aggregate = 15s, per-UF = 60s (configurable via env).

## storage.py
- Primary backend: `sqlframe_hardcharger.HardchargerSession` pointing at `HARDCHARGER_URI`.
- Fallback backend: local `duckdb.connect("data/eleicoes.duckdb")` if Hardcharger
  is unreachable at startup. Log which backend is active.
- Expose a uniform interface: `ingest(df, table)`, `query(sql, params) → DataFrame`,
  `exists(table) → bool`. Both backends must implement it.
- Tables: `raw_turbo` (append-only with ts, uf, payload_hash, payload),
  `votes_parcial` (normalized), `votes_projected`, `simulations_meta`.

## ingest.py
- Async or threaded poller for all UFs + BR aggregate.
- Writes raw payload to `raw_turbo` and normalized rows to `votes_parcial`.
- Sends a heartbeat log every cycle with: ts, uf, apurado_pct, bytes, hash, req/s.

## parse.py
- Validate against Pydantic models in `schemas.py`.
- The TSE schema varies by year/cargo: write the parser to be *tolerant* —
  map fields via a lookup table (`candidato.nm`, `candidato.v`, etc.), and
  raise a clear error if the expected keys are missing. Include one real
  fixture JSON in `tests/fixtures/` and make the parser pass on it.

## project.py
Inputs: partial votes per candidate per UF, total apurado %, historical 2022
results per UF (download from TSE open data portal at startup, cache in DuckDB).

Steps:
1. For each UF: extrapolate remaining votes using that UF's partial share if
   apurado_pct > 5%; else use the UF's 2022 share adjusted by the national
   swing observed across UFs already at >5%.
2. Aggregate to BR.
3. Monte Carlo: 5,000 Dirichlet draws. Dirichlet concentration `alpha` must be
   **dynamic**: `alpha_scale = f(apurado_pct)` — high uncertainty (<30% apurado)
   → scale=20; medium → 100; high (>80%) → 500. Document the calibration.
4. Compute: P(win 1st round), P(go to runoff), most likely runoff pairings
   (all pairs ranked by frequency), 90% CI per candidate, and a running
   time-series of the projection (store each snapshot in `votes_projected`).
5. Also compute and display the **naive linear projection** (share × 100%) as a
   sanity-check baseline alongside the model output.

## dashboard.py (Streamlit)
Layout (top → bottom):
1. Header: election name, cargo, last update time (BRT), % apurado, data source badge.
2. Big-number cards: projected winner + P(win 1st round), projected 2nd-place + P(runoff).
3. Horizontal bar chart: projected % per candidate with 90% CI error bars.
4. Line chart: projection time series (all snapshots since page opened).
5. Table: all 2nd-round pairings with probabilities, sorted descending.
6. Footer: source attribution, disclaimer, backend in use (Hardcharger/DuckDB), req/s.
- Auto-refresh every 60s via `streamlit-autorefresh`; cache TSE responses with `ttl=45`.
- Mobile-friendly. Dark mode supported.

# DEPLOYMENT (MUST PRODUCE A PUBLIC URL)
1. Push to a GitHub repo (create it if needed).
2. Deploy the Streamlit dashboard using **Streamlit Community Cloud**
   (https://share.streamlit.io) as the primary path — it's free and produces
   a public URL of the form `https://<app>.streamlit.app`.
   - Add a `runtime.txt` pinning Python 3.11 if needed.
   - Ensure `requirements.txt` includes: requests, pandas, numpy, scipy, pyarrow,
     streamlit, streamlit-autorefresh, duckdb, pydantic, sqlframe-hardcharger,
     adbc-driver-hardcharger, python-dotenv, tenacity.
3. **Fallback deploy** (if Streamlit Cloud requires manual auth you can't
   complete): produce a Dockerfile + a `render.yaml` for Render.com, AND write
   step-by-step deploy instructions in `README.md`. If Hugging Face Spaces is
   reachable, use it as an alternative (Gradio/Streamlit SDK).
4. The dashboard MUST run with the local DuckDB fallback if Hardcharger env
   vars are absent — so the public deployment works without paid infra.
5. Update `README.md` with: live URL, architecture diagram (ASCII or Mermaid),
   how to run locally, how to swap Hardcharger in, and the compliance section.

# ACCEPTANCE CRITERIA (Definition of Done)
Return a checklist confirming each item:
- [ ] `pytest` passes (parsers + projection + a dry-run fixture test).
- [ ] Ingestor respects rate limit and hash cache — show logs proving it.
- [ ] A dry run was executed against the LIVE TSE endpoint and the projected
      winner matches the current official leader (or explain the delta).
- [ ] Monte Carlo produces probabilities and a ranked 2nd-round pairing table.
- [ ] Dashboard is live at a public HTTPS URL — **paste the URL in your final message**.
- [ ] `README.md` documents everything and includes the disclaimer + attribution.
- [ ] No secrets committed (verify `git grep` for keys/passwords).
- [ ] Rate-limit compliance verified via a 5-minute log excerpt.

# OUTPUT FORMAT (your final message)
Reply with:
1. **Live dashboard URL** (or, if blocked, the exact commands I must run to finish deploy).
2. **Repo URL**.
3. The acceptance-criteria checklist, ticked.
4. A 10-line "operating notes" section: how to restart ingestion, how to swap
   Hardcharger in, how to change election/cargo, how to tune Dirichlet scale.
5. One sample screenshot-equivalent (text dump of dashboard state at deploy time).

# FALLBACK POLICY
If any dependency is unreachable (Hardcharger server, Streamlit Cloud auth, TSE
endpoint during your run), do NOT abort. Instead:
- Use the documented fallback.
- Log the failure.
- Still produce a deployable artifact + instructions.
- In your final message, clearly separate "done" from "requires your manual step".

Begin now. Work iteratively, commit often, and do not stop until the public URL
is live or you have exhausted every free-hosting fallback.
