from __future__ import annotations

from pathlib import Path

import pytest

from app.core.settings import Settings
from app.db.pool import QueryResult
from app.errors import LLMFailure, NoSafeQuery, QueryFailed
from app.llm.base import LLMClient
from app.llm.fake import FakeLLM
from app.schema_ctx.manifest import Manifest, load_manifest
from app.service.pipeline import Pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MANIFEST = REPO_ROOT / "data" / "schema" / "manifest.yaml"


class StubDatabase:
    def __init__(self, result: QueryResult | None = None, error: Exception | None = None) -> None:
        self.result = result or QueryResult(
            columns=["code"],
            rows=[["NBA"]],
            row_count=1,
            truncated=False,
        )
        self.error = error
        self.executed: list[str] = []

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        self.executed.append(sql)
        if self.error is not None:
            raise self.error
        return self.result


class RecordingCache:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.puts: list[tuple[str, dict, int]] = []

    def put(self, key: str, payload: dict, ttl_seconds: int) -> None:
        if self.error is not None:
            raise self.error
        self.puts.append((key, payload, ttl_seconds))


class ExplodingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        raise ConnectionError("provider down")


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return load_manifest(REAL_MANIFEST)


def make_pipeline(
    responses: list[str],
    *,
    manifest: Manifest,
    database: StubDatabase | None = None,
    cache: RecordingCache | None = None,
    llm: LLMClient | None = None,
    max_repair_attempts: int = 2,
) -> tuple[Pipeline, LLMClient, StubDatabase, RecordingCache]:
    settings = Settings(
        _env_file=None,
        max_repair_attempts=max_repair_attempts,
        cache_ttl_seconds=111,
    )
    resolved_llm = llm or FakeLLM(responses)
    resolved_database = database or StubDatabase()
    resolved_cache = cache or RecordingCache()
    pipeline = Pipeline(settings, manifest, resolved_llm, resolved_database, resolved_cache)
    return pipeline, resolved_llm, resolved_database, resolved_cache


def test_happy_path_returns_result_and_writes_cache(manifest: Manifest) -> None:
    pipeline, _, _, cache = make_pipeline(
        ["SELECT code FROM sports.leagues"], manifest=manifest
    )

    result = pipeline.run(
        question="list leagues", sport="basketball", max_rows=10, cache_key="abc123"
    )

    assert result.rows == [["NBA"]]
    assert result.row_count == 1
    assert result.truncated is False
    assert len(cache.puts) == 1
    key, payload, ttl = cache.puts[0]
    assert key == "abc123"
    assert ttl == 111
    assert payload["query"] == result.query


def test_repair_loop_recovers(manifest: Manifest) -> None:
    pipeline, llm, _, _ = make_pipeline(
        ["DELETE FROM sports.leagues", "SELECT code FROM sports.leagues"],
        manifest=manifest,
    )

    result = pipeline.run(question="list leagues", sport="basketball", max_rows=10)

    assert len(llm.calls) == 2
    assert "only SELECT" in llm.calls[1][-1]["content"]
    assert '"sports"."leagues"' in result.query


def test_repair_exhaustion_is_sanitized(manifest: Manifest) -> None:
    pipeline, _, _, _ = make_pipeline(
        ["DELETE FROM sports.leagues", "TRUNCATE sports.teams"],
        manifest=manifest,
    )

    with pytest.raises(NoSafeQuery) as caught:
        pipeline.run(question="list leagues", sport="basketball", max_rows=10)

    assert str(caught.value) == "the question could not be answered safely"
    assert caught.value.detail == "only SELECT statements are allowed"


def test_limit_injected_when_missing(manifest: Manifest) -> None:
    pipeline, _, database, _ = make_pipeline(
        ["SELECT code FROM sports.leagues"], manifest=manifest
    )

    pipeline.run(question="list leagues", sport="basketball", max_rows=7)

    assert "LIMIT 7" in database.executed[-1]


def test_existing_smaller_limit_kept(manifest: Manifest) -> None:
    pipeline, _, database, _ = make_pipeline(
        ["SELECT code FROM sports.leagues LIMIT 3"], manifest=manifest
    )

    pipeline.run(question="list leagues", sport="basketball", max_rows=50)

    assert "LIMIT 3" in database.executed[-1]
    assert "LIMIT 50" not in database.executed[-1]


def test_existing_larger_limit_capped(manifest: Manifest) -> None:
    pipeline, _, database, _ = make_pipeline(
        ["SELECT code FROM sports.leagues LIMIT 500"], manifest=manifest
    )

    pipeline.run(question="list leagues", sport="basketball", max_rows=50)

    assert "LIMIT 50" in database.executed[-1]
    assert "LIMIT 500" not in database.executed[-1]


def test_db_failure_is_wrapped_and_sanitized(manifest: Manifest) -> None:
    database = StubDatabase(error=RuntimeError("boom-secret"))
    pipeline, _, _, _ = make_pipeline(
        ["SELECT code FROM sports.leagues"], manifest=manifest, database=database
    )

    with pytest.raises(QueryFailed) as caught:
        pipeline.run(question="list leagues", sport="basketball", max_rows=10)

    assert str(caught.value) == "the query could not be completed"
    assert "boom-secret" not in str(caught.value)
    assert "boom-secret" in caught.value.detail


def test_llm_failure_is_wrapped(manifest: Manifest) -> None:
    pipeline, _, _, _ = make_pipeline([], manifest=manifest, llm=ExplodingLLM())

    with pytest.raises(LLMFailure) as caught:
        pipeline.run(question="list leagues", sport="basketball", max_rows=10)

    assert str(caught.value) == "the question could not be answered right now"
    assert "provider down" in caught.value.detail


def test_cache_failure_does_not_fail_request(manifest: Manifest) -> None:
    cache = RecordingCache(error=ConnectionError("redis down"))
    pipeline, _, _, _ = make_pipeline(
        ["SELECT code FROM sports.leagues"], manifest=manifest, cache=cache
    )

    result = pipeline.run(
        question="list leagues", sport="basketball", max_rows=10, cache_key="k1"
    )

    assert result.row_count == 1
    assert cache.puts == []


def test_missing_cache_key_skips_cache(manifest: Manifest) -> None:
    pipeline, _, _, cache = make_pipeline(
        ["SELECT code FROM sports.leagues"], manifest=manifest
    )

    pipeline.run(question="list leagues", sport="basketball", max_rows=10)

    assert cache.puts == []
