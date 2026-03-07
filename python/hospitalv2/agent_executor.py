from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from a2a.types import InternalError, UnsupportedOperationError
import os
from typing import Optional

from agent import HospitalAgent

VISUALIZE_GRAPH_INPUT = os.getenv("VISUALIZE_GRAPH_INPUT", "true").lower() == "true"


async def _visualize_stored_chat(label: str, agent) -> None:
    if not VISUALIZE_GRAPH_INPUT:
        return

    thread_id = getattr(agent.tools_service, "interagent_thread_id", None)
    if not thread_id:
        print(f"\n=== {label}: stored chat unavailable (missing thread id) ===")
        return

    config = {"configurable": {"thread_id": thread_id}}
    state = None
    if hasattr(agent.graph, "aget_state"):
        print("Retrieving graph state (async)...")
        state = await agent.graph.aget_state(config)
    elif hasattr(agent.graph, "get_state"):
        print("Retrieving graph state (sync)...")
        state = agent.graph.get_state(config)

    values = getattr(state, "values", None) or {}
    messages = values.get("messages", [])

    print(f"\n=== {label}: stored chat ({thread_id}) ===")
    if not messages:
        print("(empty)")
    else:
        for i, m in enumerate(messages, start=1):
            msg_type = getattr(m, "type", m.__class__.__name__)
            content = getattr(m, "content", str(m))
            print(f"[{i}] {msg_type}: {content}")
    print("=== end ===\n")


class HospitalExecutor(AgentExecutor):
    def __init__(self, agent: Optional[HospitalAgent] = None):
        self.agent = agent or HospitalAgent()



    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            msg = context.get_user_input()

            task = context.current_task
            if not task:
                task = new_task(context.message)  
                await event_queue.enqueue_event(task)

            updater = TaskUpdater(event_queue, task.id, task.context_id)

            response = await self.agent.run(
                user_text=msg,
                thread_id=self.agent.tools_service.interagent_thread_id,
            )

            await _visualize_stored_chat("HOSPITAL", self.agent)

            await updater.add_artifact(
                [Part(root=TextPart(text=str(response)))],
                name="hospital_reply",
            )
            await updater.complete()

        except Exception as e:
            raise ServerError(error=InternalError()) from e
        
        
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise ServerError(error=UnsupportedOperationError())
