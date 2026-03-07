import uvicorn
import os

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.requests import Request
from starlette.responses import JSONResponse

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

from agent_executor import HospitalExecutor
from agent import HospitalAgent


HOST = "127.0.0.1"
PORT = 2024
DEFAULT_EMPLOYEE_THREAD_ID = os.getenv("HOSPITAL_EMPLOYEE_THREAD_ID", "hospital-employee-1")


def build_agent_card():

    hospital_skill = AgentSkill(
        id="hospital_coordination",
        name="Hospital Disaster Coordination",
        description="Coordinates hospital operations and communicates with external organizations such as the Red Cross.",
        tags=["hospital", "disaster-response", "coordination"],
        examples=[
            "Coordinate ambulance dispatch with Red Cross",
            "Ask Red Cross for available volunteers",
            "Confirm triage support availability",
        ],
    )

    agent_card = AgentCard(
        name="Hospital Coordination Agent",
        description="Hospital-side agent responsible for disaster coordination and inter-organizational communication.",
        url=f"http://{HOST}:{PORT}/",
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[hospital_skill],
    )

    return agent_card


def build_server():
    shared_agent = HospitalAgent()

    request_handler = DefaultRequestHandler(
        agent_executor=HospitalExecutor(agent=shared_agent),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=build_agent_card(),
        http_handler=request_handler,
    )

    app = server.build()

    async def employee_chat(request: Request):
        body = await request.json()
        text = str(body.get("text", "")).strip()
        thread_id = str(body.get("thread_id", DEFAULT_EMPLOYEE_THREAD_ID))

        if not text:
            return JSONResponse({"error": "Missing non-empty 'text'"}, status_code=400)

        reply = await shared_agent.run(text, thread_id=thread_id)
        return JSONResponse({"reply": reply, "thread_id": thread_id})

    app.add_route("/employee/chat", employee_chat, methods=["POST"])
    return app


if __name__ == "__main__":
    app = build_server()

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
    )
