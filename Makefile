PY := $(abspath orchestrator/.venv/bin/python)

.PHONY: up down ingest ingest-once seed test-py test-go test-integration lint verify-schema

up:
	docker compose up -d --wait

down:
	docker compose down

ingest:
	@set -a; test ! -f .env || . ./.env; set +a; cd orchestrator && $(PY) -m app.retrieval.ingest

ingest-once:
	docker compose build ingestor >/dev/null
	docker compose run --rm ingestor once

seed:
	docker compose build ingestor >/dev/null
	docker compose run --rm ingestor seed

test-py:
	cd orchestrator && .venv/bin/ruff check . && .venv/bin/python -m pytest -q

test-go:
	cd gateway && gofmt -l . && go vet ./... && go test ./...
	cd ingestor && gofmt -l . && go vet ./... && go test ./...

test-integration:
	docker compose -f docker-compose.test.yml up -d
	docker compose -f docker-compose.test.yml wait migrator
	cd orchestrator && .venv/bin/python -m pytest -q

lint:
	cd orchestrator && .venv/bin/ruff check .
	cd gateway && golangci-lint run ./...
	cd ingestor && golangci-lint run ./...

verify-schema:
	docker compose -f docker-compose.test.yml down -v
	docker compose -f docker-compose.test.yml up -d
	docker compose -f docker-compose.test.yml wait migrator
	@docker compose -f docker-compose.test.yml exec -T postgres psql -U postgres -d sports -tAc "SELECT CASE WHEN count(*) = 5 THEN 'verify-schema: OK' ELSE 'verify-schema: MISSING TABLES' END FROM information_schema.tables WHERE table_schema = 'sports' AND table_name IN ('leagues', 'teams', 'players', 'games', 'player_stats');"
	@docker compose -f docker-compose.test.yml exec -T postgres psql -U postgres -d sports -tAc "SELECT CASE WHEN count(*) = 1 THEN 'ingest.watermarks: OK' ELSE 'ingest.watermarks: MISSING' END FROM information_schema.tables WHERE table_schema = 'ingest' AND table_name = 'watermarks';"
	@docker compose -f docker-compose.test.yml exec -T postgres psql -U postgres -d sports -tAc "SELECT CASE WHEN has_table_privilege('sports_writer', 'sports.games', 'INSERT,UPDATE') AND has_table_privilege('sports_reader', 'sports.games', 'SELECT') AND NOT has_table_privilege('sports_reader', 'sports.games', 'INSERT') THEN 'role privileges: OK' ELSE 'role privileges: MISMATCH' END;"
