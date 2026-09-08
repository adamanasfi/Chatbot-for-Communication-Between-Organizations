import uvicorn
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

from agent_executor import ERExecutor
from agent import ERAgent
from ui import register_ui_routes
from db_listener import register_db_listener


HOST = "127.0.0.1"
PORT = 2026
DEFAULT_EMPLOYEE_THREAD_ID = os.getenv("ER_EMPLOYEE_THREAD_ID", "er-employee-1")


def build_agent_card() -> AgentCard:
    er_skill = AgentSkill(
        id="er_coordination",
        name="Emergency Department Coordination",
        description="Coordinates Emergency Department operations and communicates with the Hospital Command Center (HCC).",
        tags=["emergency-department", "mass-casualty", "coordination", "hics"],
        examples=[
            "Send a situation report to HCC",
            "Request additional ICU beds from HCC",
            "Summarize current triage distribution",
        ],
    )

    return AgentCard(
        name="ER Coordination Agent",
        description="Emergency Department-side agent responsible for intra-hospital coordination with the Hospital Command Center.",
        url=f"http://{HOST}:{PORT}/",
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[er_skill],
    )


def build_server():
    agent = ERAgent()

    request_handler = DefaultRequestHandler(
        agent_executor=ERExecutor(agent=agent),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )

    app = server.build()
    register_ui_routes(app, agent, DEFAULT_EMPLOYEE_THREAD_ID, os.getenv("ER_DB_DSN"))
    register_db_listener(app, agent, os.getenv("ER_DB_DSN"), "er_case_updates")
    return app


if __name__ == "__main__":
    app = build_server()

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
    )
