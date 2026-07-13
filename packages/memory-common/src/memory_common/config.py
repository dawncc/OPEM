from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://memory:memory@localhost/memory"
    memory_server_host: str = "0.0.0.0"
    memory_server_port: int = 8000
    memory_llm_enabled: bool = False
    memory_llm_base_url: str = "http://localhost:11434/v1"
    memory_llm_model: str = "qwen3"
    memory_embedding_enabled: bool = False
    memory_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    memory_worker_poll_seconds: float = 1.0
    memory_daily_refresh_seconds: float = 60.0
    memory_timezone: str = "Asia/Shanghai"
    memory_recall_candidate_limit: int = 0
    memory_server_url: str = "http://127.0.0.1:8000"
    memory_model_prices_json: str = "{}"
    memory_evolution_enabled: bool = True
    memory_strategy_canary_enabled: bool = False
    memory_strategy_min_shadow_cases: int = 20
    memory_strategy_min_canary_cases: int = 50
    memory_strategy_canary_fraction: float = 0.05
    memory_strategy_max_quality_regression: float = 0.02


@lru_cache
def get_settings() -> Settings:
    return Settings()
