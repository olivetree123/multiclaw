from __future__ import annotations

from tortoise import Tortoise, connections

from memory.config import load_memory_config

TORTOISE_MODEL_MODULES = ["memory.models", "auth.models", "api.models"]


async def init_database() -> None:
    config = load_memory_config()
    await Tortoise.init(
        db_url=config.database_url,
        modules={"models": TORTOISE_MODEL_MODULES},
        _enable_global_fallback=True,
    )
    await Tortoise.generate_schemas(safe=True)
    await _configure_postgres_uuidv7_defaults(config.database_url)


async def close_database() -> None:
    await Tortoise.close_connections()


async def _configure_postgres_uuidv7_defaults(database_url: str) -> None:
    if not database_url.startswith("postgres://"):
        return

    connection = connections.get("default")
    await connection.execute_script("""
        ALTER TABLE "session" ALTER COLUMN "id" SET DEFAULT uuidv7();
        ALTER TABLE "memory" ALTER COLUMN "id" SET DEFAULT uuidv7();
        ALTER TABLE "history" ALTER COLUMN "id" SET DEFAULT uuidv7();
        ALTER TABLE "users" ALTER COLUMN "id" SET DEFAULT uuidv7();
        ALTER TABLE "api_session" ALTER COLUMN "id" SET DEFAULT uuidv7();
        ALTER TABLE "api_session_message" ALTER COLUMN "id" SET DEFAULT uuidv7();
        """)
