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

## Next

### M2 — Orchestrator pipeline (in progress)

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

**Definition of done:** `pytest` green incl. pipeline tests against the fake LLM + integration tests on `docker-compose.test.yml` deps (postgres). Chroma/vector pieces deferred to M4.

### M3 — Gateway (Go)

- First: `contracts/openapi.orc.yaml` — single source of truth for the Go↔Python API
- `config/` (`GATEWAY_*`, never `DATABASE_URL`), `server/` router + graceful shutdown
- `middleware/` — requestid, authn, Redis-backed ratelimit, logging, recovery
- `handler/` — `POST /api/v1/query`, `healthz`/`readyz`
- `cache/` — interface + Redis impl, key = `sha256(sport‖question‖max_rows)`
- `orchestrator/` — REST client, per-call hard timeout
- `model/` — DTOs mirroring the contract

**DoD:** unit tests; cache-first flow: query → miss → orchestrator → 200 → repeat → `X-Cache: HIT`.

### M4 — Real LLM + Chroma

- Slot real provider into `ORC_LLM_*` (provider TBD)
- `data/corpus/questions.jsonl` → Chroma via `make ingest`; few-shot retrieval into prompts

### M5 — E2E smoke

- `make seed` → gateway query → 200 → repeat → `X-Cache: HIT`
- Full Makefile target set: `up/down, seed, ingest-once, test-go/py/integration, verify-schema, lint`

## Ingestor track (~5 sessions, parallel to M2–M4)

| # | Module | Checkpoint |
|---|---|---|
| I1 | Skeleton — `go.mod`, `INGESTOR_*` config, logging, `main.go` modes (`serve\|once\|seed`) | builds + vet + lint clean |
| I2 | Watermark — durable cursors over `ingest.watermarks` | round-trip + crash-mid-batch resume |
| I3 | Extract + Source — retry/backoff/jitter, Source interface + registry, **synthetic source**, `configs/sources.yaml` | induced failures show backoff |
| I4 | Transform + Load — canonical rows → natural-key upserts (pgx batches) | `make seed` twice → identical row counts |
| I5 | Scheduler + wiring — serve loop, compose service, Makefile targets | `make ingest-once` E2E |

## Decision ledger

| Decision | Choice |
|---|---|
| Transport | REST + shared OpenAPI contract (gRPC-ready later) |
| Query flow | Synchronous, hard timeout enforced by Go |
| Migrations | Plain SQL + one-shot migrator container |
| LLM provider | TBD — provider-agnostic interface, env-selected (`ORC_LLM_*`) |
| Live data sources | TBD — pluggable Source adapters via `configs/sources.yaml` |
| Cache freshness | TTL-only (no invalidation hook) |
| DB roles | `sports_ddl` migrator · `sports_writer` ingestor · `sports_reader` orchestrator |
| Generated code | No comments; no commits without prior review |

## Build-order rationale

Safety gate is built and tested **before any LLM can generate real SQL**. Pipeline proves the flow against a deterministic fake before provider lock-in. Gateway caches against a stable orchestrator API. Real LLM + corpus land last, on top of gates that are already adversarially tested.
