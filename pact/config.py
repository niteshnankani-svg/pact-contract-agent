from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PACT_", env_file=".env", extra="ignore")
    mode: str = "production"
    database_url: str = "sqlite:///runtime/pact.db"
    checkpoint_url: str = "runtime/checkpoints.db"
    api_keys: dict[str, dict[str, str]] = {}
    provider: str = "ollama"
    model: str = "gemma3:1b"
    model_url: str = "http://127.0.0.1:11434"
    model_api_key: str = ""
    retrieval: str = "lexical"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    neural_profile: str = 'compact'
    embedding_path: str = ''
    reranker_path: str = ''
    model_timeout: int = 90
    max_model_calls: int = 12
    max_documents: int = 20
    max_text_chars: int = 120_000
    max_upload_bytes: int = 3_000_000
    max_jobs: int = 50
    lease_seconds: int = 240
    artifact_dir: str = "runtime/artifacts"
    github_repo: str = ""
    github_token: str = ""
    enable_github_actions: bool = False
    enable_ocr: bool = False

    def prepare(self):
        if self.mode not in {"demo", "production", "test"}:
            raise ValueError("PACT_MODE must be demo, test or production")
        if self.provider not in {"ollama", "openai-compatible", "test"}:
            raise ValueError("Unknown provider")
        if self.retrieval not in {'lexical', 'hybrid'}:
            raise ValueError('Retrieval must be lexical or hybrid')
        if self.neural_profile not in {'compact', 'multilingual'}:
            raise ValueError('Neural profile must be compact or multilingual')
        if self.mode == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires PostgreSQL")
            if not self.checkpoint_url.startswith("postgresql://"):
                raise ValueError("Production requires PostgreSQL checkpoints")
            if not self.api_keys or self.provider == "test":
                raise ValueError("Production requires configured credentials and a real model provider")
        if not 1 <= self.model_timeout <= 120 or not 1 <= self.max_model_calls <= 20:
            raise ValueError("Invalid model budget")
        for path in [Path("runtime"), Path(self.artifact_dir)]:
            path.mkdir(parents=True, exist_ok=True)
