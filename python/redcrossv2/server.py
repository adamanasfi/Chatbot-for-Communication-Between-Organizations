# redcrossv2/server.py
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

from agent_executor import RedCrossExecutor
from agent import RedCrossAgent


HOST = "127.0.0.1"
PORT = 2025
DEFAULT_EMPLOYEE_THREAD_ID = os.getenv("REDCROSS_EMPLOYEE_THREAD_ID", "redcross-employee-1")


def build_agent_card() -> AgentCard:
    red_cross_skill = AgentSkill(
        id="red_cross_coordination",
        name="Red Cross Disaster Coordination",
        description="Coordinates Red Cross operations and communicates with partner organizations such as hospitals.",
        tags=["red-cross", "disaster-response", "coordination", "logistics"],
        examples=[
            "Coordinate ambulance dispatch with hospital",
            "Request hospital receiving instructions",
            "Negotiate priorities across multiple incidents",
        ],
    )

    return AgentCard(
        name="Red Cross Coordination Agent",
        description="Red Cross-side agent responsible for disaster coordination and inter-organizational communication.",
        url=f"http://{HOST}:{PORT}/",
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[red_cross_skill],
    )


def build_server():
    shared_agent = RedCrossAgent()

    request_handler = DefaultRequestHandler(
        agent_executor=RedCrossExecutor(agent=shared_agent),
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
