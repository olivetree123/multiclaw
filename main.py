import uuid
import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv

from agent import AgentRunner

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
def main(workspace: Path | None, session_id: uuid.UUID | None):
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    runner = AgentRunner(
        workspace=resolved_workspace,
        session_id=str(session_id) if session_id else None,
    )
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
