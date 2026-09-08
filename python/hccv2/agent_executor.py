from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from a2a.types import InternalError, UnsupportedOperationError
from typing import Optional

from agent import HCCAgent


class HCCExecutor(AgentExecutor):
    def __init__(self, agent: Optional[HCCAgent] = None):
        self.agent = agent or HCCAgent()

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
                thread_id=self.agent.intercoord_thread_id,
            )

            await updater.add_artifact(
                [Part(root=TextPart(text=str(response)))],
                name="hcc_reply",
            )
            await updater.complete()

        except Exception as e:
            raise ServerError(error=InternalError()) from e

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())
