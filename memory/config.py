from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryConfig:
    database_url: str
    qdrant_url: str
    qdrant_api_key: str | None
    qdrant_collection: str
    user_id: str
    search_limit: int
    summary_interval_seconds: float
    model_max_context_tokens: int
    summary_trigger_ratio: float
    summary_batch_max_tokens: int
    recent_history_max_messages: int
    recent_history_token_ratio: float
    recent_history_token_limit: int
    embedding_dimensions: int
    llm_model: str
    llm_base_url: str | None
    llm_api_key: str | None


def load_memory_config() -> MemoryConfig:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL is required for the memory app.")
    database_url = _normalize_database_url(database_url)
    model_max_context_tokens = int(os.getenv("MEMORY_MODEL_MAX_CONTEXT_TOKENS", "64000"))
    summary_batch_max_ratio = float(os.getenv("MEMORY_SUMMARY_BATCH_MAX_RATIO", "0.5"))
    recent_history_token_ratio = float(os.getenv("MEMORY_RECENT_HISTORY_TOKEN_RATIO", "0.1"))

    return MemoryConfig(
        database_url=database_url,
        qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY"),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "memory_items"),
        user_id=os.getenv("MEMORY_USER_ID", "default_user"),
        search_limit=int(os.getenv("MEMORY_SEARCH_LIMIT", "8")),
        summary_interval_seconds=float(os.getenv("MEMORY_SUMMARY_INTERVAL_SECONDS", "10")),
        model_max_context_tokens=model_max_context_tokens,
        summary_trigger_ratio=float(os.getenv("MEMORY_SUMMARY_TRIGGER_RATIO", "0.66")),
        summary_batch_max_tokens=int(model_max_context_tokens * summary_batch_max_ratio),
        recent_history_max_messages=int(os.getenv("MEMORY_RECENT_HISTORY_MAX_MESSAGES", "20")),
        recent_history_token_ratio=recent_history_token_ratio,
        recent_history_token_limit=int(model_max_context_tokens * recent_history_token_ratio),
        embedding_dimensions=int(os.getenv("MEMORY_EMBEDDING_DIMENSIONS", "384")),
        llm_model=os.getenv("MEMORY_SUMMARY_MODEL",
                            os.getenv("LLM_MODEL", "deepseek/deepseek-v4-pro")),
        llm_base_url=os.getenv("MEMORY_SUMMARY_BASE_URL",
                               os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
        llm_api_key=os.getenv("MEMORY_SUMMARY_API_KEY", os.getenv("DEEPSEEK_API_KEY")),
    )


def _normalize_database_url(database_url: str) -> str:
    """Accept common SQLAlchemy-style Postgres URLs while using Tortoise underneath."""
    replacements = {
        "postgresql+psycopg://": "postgres://",
        "postgresql+asyncpg://": "postgres://",
        "postgresql://": "postgres://",
    }
    for source, target in replacements.items():
        if database_url.startswith(source):
            return database_url.replace(source, target, 1)
    return database_url
