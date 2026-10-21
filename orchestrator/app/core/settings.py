from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORC_", env_file=".env", extra="ignore")

    app_name: str = "sports-orchestrator"
    log_level: str = "INFO"
    llm_provider: str = "fake"
    llm_timeout_seconds: float = 30.0
    max_repair_attempts: int = 2
    database_url: str = "postgresql://orchestrator:dev_orchestrator@localhost:5432/sports"
    db_max_connections: int = 5
    db_statement_timeout_ms: int = 5000
    manifest_path: str = ""
    cache_redis_url: str = ""
    cache_ttl_seconds: int = 300

    def resolve_manifest_path(self) -> Path:
        if self.manifest_path:
            return Path(self.manifest_path)
        candidates = (
            Path.cwd() / "data" / "schema" / "manifest.yaml",
            Path(__file__).resolve().parents[3] / "data" / "schema" / "manifest.yaml",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[-1]
