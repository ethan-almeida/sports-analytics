# sports-analytics — Project Plan

**Status as of 2026-10-21.** Reflects completed work (with evidence) and what remains, in execution order. Living document — update the status tables as modules land.

## System at a glance

Four services around one Postgres:

| Service | Lang | Role |
|---|---|---|
| `gateway` | Go | Public entry/exit — auth, rate limit, cache-first query flow |
| `orchestrator` | Python | AI + data logic — prompt build, LLM, safety gate, read-only queries |
| `ingestor` | Go | ETL — pluggable sources → transform → idempotent upserts |
| `migrator` | shell/SQL | One-shot container, plain SQL migrations |

Plus: `contracts/openapi.orc.yaml` as the single Go↔Python contract, `data/schema/manifest.yaml` as the single query whitelist.

## Working agreements

- **Code-then-test loop**: one block at a time; when a module is complete we run its checkpoint (build/lint + targeted behavior tests) before moving on.
- **No comments in newly generated code** (agreed 2026-10-21). Existing files may retain comments until reviewed.
- **No commits without your review** — nothing gets `git commit`ed unless you approve it first.

## Completed

### M0 — Data layer + roles + migrator ✅ (2026-10-19)

- `data/postgres-init/` — roles `sports_ddl`/`sports_writer`/`sports_reader` (NOLOGIN groups) + login users `migrator`/`ingestor`/`orchestrator` with env-driven passwords
- `data/migrations/0001…0004` — `sports` schema (leagues, teams, games, players, player_stats; natural-key PKs, FKs, status/score CHECKs, grants) + `ingest` schema (watermarks) — reader has **zero** access to `ingest.*`
- `data/migrator/migrate.sh` — one-shot, filename-ordered, per-file transaction (`--single-transaction`), tracked in `migrator.schema_migrations`, idempotent re-runs
- `docker-compose.yml` — postgres (healthcheck, init mounts) + migrator (depends_on healthy)
- `.env.example` — every env var documented; dev defaults boot with an empty `.env`

**Checkpoint evidence:** 7 roles; 7 tables all owned by `sports_ddl`; `apply ×4` in order; writer upserts / reader selects pass; `ingest.*` + `migrator.*` denied to wrong roles; re-run = `skip ×4`.

### M1 — Safety gate ✅ (2026-10-21)

- `data/schema/manifest.yaml` — the query whitelist: 5 tables, column types mirroring migrations, per-column descriptions, 6 join relations; `ingest.*` invisible by omission
- `app/schema_ctx/manifest.py` — strict typed loader (`frozen + extra=forbid`); missing/typo'd manifests fail loudly
- `app/errors.py` — `OrchestratorError` hierarchy: `str(exc)` is always a generic user message, specifics on `.detail` (logs only)
- `app/safety/validation.py` — 6 fail-fast checks → returns **rewritten canonical SQL** (qualified, `SELECT *` expanded) that the db layer executes as-is:
  1. exactly one statement
  2. SELECT-only (no DML/DDL/EXPLAIN/COPY; no `FOR UPDATE/SHARE`)
  3. deny-listed functions anywhere (incl. CTEs): `pg_sleep*`, `pg_read*`, `dblink*`, `lo_import/export`, backend killers
  4. table whitelist — unqualified names forced into default schema (blocks `FROM pg_class` smuggling)
  5. column whitelist via qualify() against a manifest-derived schema dict
  6. independent second column pass
- `tests/test_safety_validation.py` — adversarial corpus: 9 allowed shapes, 23 denied attacks, 9 self-stability properties, 1 leak-proofing test

**Checkpoint evidence:** `49 passed` (7 manifest + 42 safety), ruff clean.

### Housekeeping ✅ (2026-10-21)

- `.gitignore` corrected — it previously **ignored `.env.example`** (inverted); now tracks `.env.example`, ignores `.env*`, Python envs/caches, future Go build artifacts.
- (2026-10-26) the inversion had regressed in practice: `.env.example` and `docker-compose.test.yml` were still being ignored; both lines fixed, `data/chroma/` added as ignored build artifact.

### M4 — Real LLM + Chroma few-shot ✅ (2026-10-26)

- `app/llm/openai_compat.py` — `OpenAICompatibleClient` over the `openai` SDK: base-url driven, Groq is just `ORC_LLM_BASE_URL=https://api.groq.com/openai/v1`; maps 401/429/API errors/timeouts/empty completions to `LLMFailure` (`.detail` preserved through the pipeline wrap via an `except OrchestratorError: raise` pass-through); SDK retries disabled (repair loop owns retries)
- `app/llm/__init__.py` — factory takes `ORC_LLM_PROVIDER=fake|groq`, `groq` requires `ORC_LLM_API_KEY` else startup error
- `data/corpus/questions.jsonl` — 28 canonical `{sport, question, sql}` exemplars grounded in the real schema (basketball/esports/synthetic/all), no data-dependent slugs; includes CASE pivots, two-alias team joins, wins computation, `RANK()` window CTE, `NOT EXISTS`, `ILIKE`-free name filters
- `app/retrieval/corpus.py` — strict pydantic loader, line-numbered errors
- `app/retrieval/store.py` — `QuestionStore` over chromadb `PersistentClient` (`data/chroma/`, default MiniLM embeddings downloaded on first run); sha256(sport‖question) ids → idempotent upsert; `similar()` filters by sport, falls back to unfiltered, respects `ORC_FEW_SHOT_K` (0 disables)
- `app/retrieval/ingest.py` + root `Makefile` — `make ingest` runs **every corpus SQL through the safety gate + row cap** before upsert (corpus drift fails loudly); loads root `.env` into env before launch
- `app/schema_ctx/prompt.py` / `app/service/pipeline.py` — few-shot exemplar user/assistant pairs injected between system rules and the real question; retrieval failure **degrades to zero exemplars** (warning logged, query proceeds)
- ~~`orchestrator/scripts/smoke_llm.py` + `scripts/smoke_seed.sql` + `make smoke-llm`~~ — TEMPORARY verification suite, **deleted after user-verified Groq pass (2026-10-26)**

**Checkpoint evidence:** `pytest` 128 passed / 2 skipped (provider error-mapping incl. 429/401/503/timeout over mock transport, corpus loader adversarial shapes, retrieval round-trip + idempotence + sport fallback + zero-k, corpus gate ×28, few-shot reach-the-LLM + degradation), `ruff` clean. Live: `make ingest ×2 → collection size 28` (idempotent); real DB seeded (8 games / 25 stat rows) + full pipeline smoke with fake provider + `ORC_FEW_SHOT_K=3` → 5/5 answered, zero degraded-retrieval warnings; retriever probe returns exemplars incl. sport-miss fallback. **Groq real-key smoke owned by user — PASSED** (first attempt silently ran the FakeLLM because the key landed in `.env.example` instead of `.env`; key rotated, example restored, and the FakeLLM now announces itself at startup via the `orchestrator config` log line, incl. a loud `provider_warning` when provider=fake).

### M3 — Gateway (Go) ✅ (2026-10-26)

- `contract/openapi.orc.yaml` — single source of truth for both surfaces: gateway (`POST /api/v1/query`, `healthz`, `readyz`) and orchestrator (`POST /v1/orchestrate`, `healthz`, `readyz`); mirrors `app/api/v1/schemas.py` bounds exactly (sport ≤32, question ≤2000, max_rows 1–1000 default 50); documents the cache-key recipe `sha256(sport ‖ "\x1f" ‖ question ‖ "\x1f" ‖ max_rows)` and `X-Cache: HIT|MISS`
- `gateway/config/` — `GATEWAY_*` only (no `DATABASE_URL`); unset Redis/API key degrades cache and authn/ratelimit off
- `gateway/model/` — DTOs mirroring the contract; validation bounds + default injection
- `gateway/orchestrator/` — REST client, per-call hard timeout (`ErrTimeout` vs `ErrUnavailable`), error-envelope passthrough, 16 MB response cap
- `gateway/cache/` — `ResponseStore` interface + `NoopStore` + Redis store; sha256 key builder with unit separator
- `gateway/middleware/` — requestid (echo-or-generate, sanitized), authn (constant-time `X-API-Key`), Redis fixed-window ratelimit (per API key else IP, fail-open, `Retry-After` on 429), JSON slog logging with request ids, panic recovery → 500 envelope
- `gateway/handler/` — cache-first flow (`X-Cache: HIT|MISS`, undecodable stored payload degrades to miss), orchestrator error proxying (400/422/500/502 verbatim; timeout→504; down→502 `orchestrator_unavailable`), `healthz`/`readyz` (redis ping timeout 2s)
- `gateway/server/` — chain Recover → RequestID → Logging → mux; authn+ratelimit only on `POST /api/v1/query`; graceful shutdown (SIGINT/SIGTERM → 10 s drain)
- `.env.example` — real `GATEWAY_*` vars; `ORC_CACHE_REDIS_URL` documented to point at the same redis for HIT flow

**Checkpoint evidence:** `go test ./...` — 5 packages, 48 tests / 72 runs (incl. subtests) all green: config defaults+overrides, model bounds, sha256 key properties, redis round-trip vs miniredis, client timeout/passthrough/garbage-body, ratelimit window reset + per-key + fail-open, authn 401s, logging fields, recovery, health, **cache-first flow** (miss→orchestrator→200→repeat→`X-Cache: HIT`, orchestrator called once, staged key verified) and **full `server.Run` e2e** (free port, real chain, MISS→HIT, SIGTERM → graceful drain). `go vet` + `golangci-lint` clean (`0 issues`), `gofmt` clean.

## Next

### M2 — Orchestrator pipeline — COMPLETE (closed out 2026-10-26 via the test stack + M5 containerization)

Integration tests demonstrated green on `docker-compose.test.yml` (130 passed / 0 skipped, 2026-10-26); orchestrator Dockerfile + compose service landed with M5.

| Piece | Contents |
|---|---|
| `app/core/` | pydantic-settings (`ORC_*`), JSON structured logging |
| `app/api/v1/` | `POST /v1/orchestrate`, `healthz`/`readyz`, DI via `deps.py` |
| `app/llm/` | provider-agnostic client, env-selected (`ORC_LLM_*`); **deterministic fake provider** ships first |
| `app/service/pipeline.py` | fixed order: manifest slice → prompt → LLM → safety gate → db → sanitized response |
| `app/service/retry.py` | bounded repair loop — gate failure detail fed back to LLM, N≤2 re-asks |
| `app/schema_ctx/` | prompt slice builder (manifest → compact schema/relations block) |
| `app/db/` | read-only pool (`orchestrator` login, `statement_timeout`, row caps) |
| `app/cache/writer.py` | writes response under Go's `cache_key` + TTL |

**Definition of done:** `pytest` green incl. pipeline tests against the fake LLM + integration tests on `docker-compose.test.yml` deps (postgres). Chroma/vector pieces landed via M4 below.

### M5 — E2E smoke — COMPLETE (2026-10-26)

- `make seed` → gateway query → 200 → repeat → `X-Cache: HIT` ✅
  Live evidence: `POST :8080/api/v1/query` → **200 + `X-Cache: MISS`** (Groq SQL through the real pipeline, 2291 ms; correct answer `Alpha One / 41 / SYN-001`); repeat → **200 + `X-Cache: HIT`** (identical payload served by Redis in sub-ms); a different question → fresh key → `MISS` again (per-question cache key granularity demonstrated)
- Full Makefile target set ✅ — `up/down/ingest/ingest-once/seed/test-py/test-go/test-integration/lint/verify-schema` all demonstrated: `test-py` 130 passed (2 integration incl.), `test-go` all packages, `lint` 0 issues across orchestrator/gateway/ingestor, `verify-schema` reports `verify-schema: OK` + `ingest.watermarks: OK` + `role privileges: OK` after a from-zero migration on the isolated test stack
- Compose wiring ✅ — `redis:8-alpine` (healthchecked), `orchestrator` (multi-stage python:3.14-slim image, deps + MinIM embedding model baked at build so no runtime model downloads; manifest + `data/chroma` mounted; healthcheck on `/healthz`; depends on postgres healthy / migrator completed / redis healthy), `gateway` (multi-stage golang:1.27-alpine → distroless/nonroot; depends on orchestrator healthy + redis healthy; publishes :8080), `ingestor` (serve-mode daemon). Compose interpolates the root `.env` so one file drives host runs and the containerized stack; container URLs are service-DNS-locked, passwords flow from `.env`
- Known catch fixed during M5: `docker compose up --wait` on stacks where nothing depends on the one-shot migrator exits 1 even when it exits 0 — test-stack targets wait explicitly via `docker compose wait migrator` instead

## Ingestor track (~5 sessions, parallel to M2–M4) — COMPLETE (2026-10-26)

| # | Module | Checkpoint | Evidence |
|---|---|---|---|
| I1 | Skeleton — `go.mod`, `INGESTOR_*` config, logging, `main.go` modes (`serve\|once\|seed`) | builds + vet + lint clean | ✅ `go vet ./...`, `golangci-lint` 0 issues, all unit tests green |
| I2 | Watermark — durable cursors over `ingest.watermarks` | round-trip + crash-mid-batch resume | ✅ `watermark` store tests + scripted `Runner` tests against postgres 5433: crash after batch 1 leaves watermark at page 1; resume skips page 0 |
| I3 | Extract + Source — retry/backoff/jitter, Source interface + registry, **synthetic source**, `configs/sources.yaml` | induced failures show backoff | ✅ `extract/retry_test.go`: attempts=3 with growing jitter-bounded sleeps; unretryable + ctx-cancel stop immediately |
| I4 | Transform + Load — canonical rows → natural-key upserts (pgx batches) | `make seed` twice → identical row counts | ✅ fresh test DB: 3/7/8/8/25 both runs; terminal watermark `{page: 4}`; `once` from terminal → 0-row no-op |
| I5 | Scheduler + wiring — serve loop, compose service, Makefile targets | `make ingest-once` E2E | ✅ compose-built image; `make seed` ×2 → identical dev-DB counts; `make ingest-once` no-op; serve-mode daemon `Up (Healthy)` in dev compose |

Layout: `model` (canonical rows) → `extract` (Source/registry/retry) → `sources` (synthetic factory) → `watermark` → `store` (pgx batch upserts) → `ingest` (Runner) → `main.go` (serve/once/seed).

## Decision ledger

| Decision | Choice |
|---|---|
| Transport | REST + shared OpenAPI contract (gRPC-ready later) |
| Query flow | Synchronous, hard timeout enforced by Go |
| Migrations | Plain SQL + one-shot migrator container |
| LLM provider | **Groq Cloud** (2026-10-26) — OpenAI-compatible client, `ORC_LLM_BASE_URL` keeps it swappable |
| Few-shot store | Chroma `PersistentClient`, in-process; default MiniLM embeddings; swap to a server container at M5 if warranted |
| Live data sources | TBD — pluggable Source adapters via `configs/sources.yaml` |
| Cache freshness | TTL-only (no invalidation hook) |
| DB roles | `sports_ddl` migrator · `sports_writer` ingestor · `sports_reader` orchestrator |
| Generated code | No comments; no commits without prior review |
| Ingestor paging | Each stream is a cursor-paged batch loop; one batch = one transaction + one watermark save; replay-after-crash is safe because upserts are natural-key idempotent |
| Synthetic dataset | Deterministic fixture mirroring the E2E smoke shape (3 leagues / 7 teams / 8 players / 8 games / 25 stat rows), paged in FK-safe order; doubles as the real `make seed` |
| Retry policy | 4 attempts max, exponential backoff × jitter (full jitter), all fetch/upsert errors retryable except ctx-cancel/deadline; watermark saves excluded from (idempotent, tiny) retry wrapper |
| Orchestrator provider visibility | Startup log line `orchestrator config` prints provider/model/few_shot_k and warns loudly on the FakeLLM — misconfigured `.env` can never silently run the canned provider (learned from the M4 smoke incident) |
| Container images | Orchestrator: multi-stage `python:3.14-slim`, deps + MiniLM model baked at build (no runtime downloads, no network surprises); manifest + `data/chroma` are runtime mounts owned by the host. Gateway: `golang:1.27-alpine` build → distroless/nonroot runtime. Ingestor: `golang:1.27-alpine` build → alpine runtime with the sources config baked |
| Compose env flow | Root `./.env` is interpolated by compose into container environments; network topology (service DNS, ports) is hard-coded in compose, credentials/knobs flow from `.env`; host-side localhost URLs in `.env`/`.env.example` are for non-container runs |
| Cache key discipline | Gateway reads Redis with `sha256(sport‖question‖max_rows)`; orchestrator writes the same key because the gateway forwards it as `cache_key` — the X-Cache HIT/MISS contract never depends on both sides re-deriving keys |

## Build-order rationale

Safety gate is built and tested **before any LLM can generate real SQL**. Pipeline proves the flow against a deterministic fake before provider lock-in. Gateway caches against a stable orchestrator API. Real LLM + corpus land last, on top of gates that are already adversarially tested.
