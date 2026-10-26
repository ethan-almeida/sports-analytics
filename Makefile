PY := $(abspath orchestrator/.venv/bin/python)

.PHONY: ingest test-go ingest-once seed

ingest:
	@set -a; test ! -f .env || . ./.env; set +a; cd orchestrator && $(PY) -m app.retrieval.ingest

test-go:
	cd gateway && go test ./...
	cd ingestor && go test ./...

ingest-once:
	docker compose build ingestor >/dev/null
	docker compose run --rm ingestor once

seed:
	docker compose build ingestor >/dev/null
	docker compose run --rm ingestor seed
