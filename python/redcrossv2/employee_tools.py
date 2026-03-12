from __future__ import annotations

from typing import Any, Optional

from langchain_core.tools import tool


class RedCrossEmployeeTools:
    def __init__(self, coordinator_agent: Optional[Any] = None):
        self.coordinator_agent = coordinator_agent
        self.ask_redcross_coordinator_tool = tool(self.ask_redcross_coordinator)

    async def ask_redcross_coordinator(self, request_text: str) -> str:
        """
        Delegate an external-coordination request from the Red Cross employee assistant
        to the local Red Cross Coordinator Agent.
        """
        if self.coordinator_agent is None:
            return "[COORDINATOR_UNAVAILABLE] Red Cross coordinator agent is not configured."

        response = await self.coordinator_agent.run(
            user_text=request_text,
            thread_id=self.coordinator_agent.handoff_thread_id,
        )
        return response
