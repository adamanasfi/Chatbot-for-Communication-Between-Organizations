from pathlib import Path

from common.console_ui import register_console_routes

STATIC_DIR = Path(__file__).parent / "static"


def register_ui_routes(
    app, agent, default_employee_thread_id: str, dsn: str
) -> None:
    register_console_routes(app, agent, default_employee_thread_id, dsn, STATIC_DIR)
