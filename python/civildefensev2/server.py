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

from agent_executor import CivilDefenseExecutor
from agent import CivilDefenseAgent
from ui import register_ui_routes
from db_listener import register_db_listener


HOST = "127.0.0.1"
PORT = 2024
DEFAULT_EMPLOYEE_THREAD_ID = os.getenv("CIVIL_DEFENSE_EMPLOYEE_THREAD_ID", "civil-defense-employee-1")


def build_agent_card():

    civil_defense_skill = AgentSkill(
        id="civil_defense_coordination",
        name="Civil Defense Coordination",
        description="Coordinates civil defense operations and communicates with external organizations such as the Red Cross.",
        tags=["civil-defense", "disaster-response", "coordination"],
        examples=[
            "Coordinate ambulance dispatch with Red Cross",
            "Ask Red Cross for available volunteers",
            "Confirm triage support availability",
        ],
    )

    agent_card = AgentCard(
        name="Civil Defense Coordination Agent",
        description="Civil defense-side agent responsible for disaster coordination and inter-organizational communication.",
        url=f"http://{HOST}:{PORT}/",
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[civil_defense_skill],
    )

    return agent_card


def build_server():
    agent = CivilDefenseAgent()

    request_handler = DefaultRequestHandler(
        agent_executor=CivilDefenseExecutor(agent=agent),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )

    app = server.build()
    register_ui_routes(app, agent, DEFAULT_EMPLOYEE_THREAD_ID, os.getenv("CIVILDEFENSE_DB_DSN"))
    register_db_listener(app, agent, os.getenv("CIVILDEFENSE_DB_DSN"), "civildefense_case_updates")
    return app


if __name__ == "__main__":
    app = build_server()

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
    )
