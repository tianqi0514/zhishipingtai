from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "传神智库"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    app_secret_key: str = "dev-only-change-this-secret-at-least-32-bytes"
    access_token_minutes: int = 480
    application_access_token_minutes: int = 15
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "Admin@123456"

    database_url: str = "sqlite:///./data/platform.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_always_eager: bool = False

    object_store_endpoint: str = "localhost:9000"
    object_store_access_key: str = "semantica"
    object_store_secret_key: str = "semantica-dev-secret"
    object_store_bucket: str = "knowledge"
    object_store_secure: bool = False
    local_storage_path: Path = Path("./data/objects")
    use_local_object_store: bool = False

    semantica_required_version: str = "0.6.6"
    kimi_api_key_file: Path = Path("/run/secrets/kimi_api_key")
    max_upload_bytes: int = 100 * 1024 * 1024
    max_image_upload_bytes: int = 50 * 1024 * 1024
    max_audio_upload_bytes: int = 500 * 1024 * 1024
    max_video_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_archive_upload_bytes: int = 500 * 1024 * 1024
    provenance_storage_path: Path = Path("./data/provenance.db")
    allowed_origins: str = "http://localhost:8080"
    opensearch_url: str = "http://localhost:9200"
    qdrant_url: str = "http://localhost:6333"
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    source_scheduler_seconds: int = 60
    source_mount_roots: str = "/app/data/sources"
    source_private_host_allowlist: str = "minio,postgres,opensearch,rabbitmq,source-fixture,webdav-fixture,ftp-fixture,sftp-fixture"
    knowledge_auto_process: bool = True
    agent_runtime_url: str = "http://agent-runtime:8090"
    agent_service_secret_file: Path = Path("/run/secrets/agent_service_secret")
    agent_access_token_minutes: int = 5
    agent_request_timeout_seconds: int = 600

    @model_validator(mode="after")
    def production_secrets_must_be_explicit(self) -> "Settings":
        if self.environment == "production":
            weak = {
                "dev-only-change-this-secret-at-least-32-bytes",
                "Admin@123456",
                "semantica-dev-secret",
            }
            if (
                self.app_secret_key in weak
                or self.bootstrap_admin_password in weak
                or self.object_store_secret_key in weak
            ):
                raise ValueError("Production secrets must be supplied through environment variables")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
