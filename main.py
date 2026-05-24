import uuid
import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv

from agent import AgentRunner, MultiAgentRunner

load_dotenv(dotenv_path="./docker/.env")


@click.command()
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Restrict file and directory tools to this workspace directory.",
)
@click.option(
    "--session-id",
    type=click.UUID,
    default=None,
    help="Continue an existing conversation session. If omitted, a new session id is generated.",
)
@click.option(
    "--multi-agent",
    is_flag=True,
    default=False,
    help="Run multi-agent mode: requirements → prototype → backend → integration → maintenance.",
)
@click.option(
    "--continue",
    "continue_maintenance",
    is_flag=True,
    default=False,
    help="Skip the development flow and enter maintenance mode for an existing project.",
)
@click.option(
    "--project-root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=".",
    help="Project root for multi-agent mode. Creates docs/, backend/, frontend/ subdirectories.",
)
@click.option(
    "--serve",
    is_flag=True,
    default=False,
    help="Start FastAPI server for multi-agent HTTP API.",
)
@click.option("--host", default="0.0.0.0", help="API server host.")
@click.option("--port", default=8000, type=int, help="API server port.")
def main(
    workspace: Path | None,
    session_id: uuid.UUID | None,
    multi_agent: bool,
    continue_maintenance: bool,
    project_root: Path,
    serve: bool,
    host: str,
    port: int,
):
    if serve:
        import uvicorn

        uvicorn.run("api.app:app", host=host, port=port)
        return

    resolved_session_id = str(session_id) if session_id else None

    if multi_agent or continue_maintenance:
        runner = MultiAgentRunner(
            project_root=project_root.expanduser().resolve(),
            session_id=resolved_session_id,
            continue_maintenance=continue_maintenance,
        )
        asyncio.run(runner.run())
        return

    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    runner = AgentRunner(
        workspace=resolved_workspace,
        session_id=resolved_session_id,
    )
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
