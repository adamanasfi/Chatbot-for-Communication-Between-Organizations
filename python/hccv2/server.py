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

from agent_executor import HCCExecutor
from agent import HCCAgent
from ui import register_ui_routes
from db_listener import register_db_listener


HOST = "127.0.0.1"
PORT = 2027
DEFAULT_EMPLOYEE_THREAD_ID = os.getenv("HCC_EMPLOYEE_THREAD_ID", "hcc-employee-1")


def build_agent_card() -> AgentCard:
    hcc_skill = AgentSkill(
        id="hcc_coordination",
        name="Hospital Command Center Coordination",
        description="Coordinates hospital-wide resource allocation and communicates with departments such as the Emergency Department (ED).",
        tags=["hospital-command-center", "mass-casualty", "coordination", "hics"],
        examples=[
            "Review the ED's latest situation report",
            "Approve an ICU bed allocation for the ED",
            "Summarize hospital-wide resource availability",
        ],
    )

    return AgentCard(
        name="HCC Coordination Agent",
        description="Hospital Command Center-side agent responsible for intra-hospital coordination with the Emergency Department.",
        url=f"http://{HOST}:{PORT}/",
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[hcc_skill],
    )


def build_server():
    agent = HCCAgent()

    request_handler = DefaultRequestHandler(
        agent_executor=HCCExecutor(agent=agent),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )

    app = server.build()
    register_ui_routes(app, agent, DEFAULT_EMPLOYEE_THREAD_ID, os.getenv("HCC_DB_DSN"))
    register_db_listener(app, agent, os.getenv("HCC_DB_DSN"), "hcc_case_updates")
    return app


if __name__ == "__main__":
    app = build_server()

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
    )
