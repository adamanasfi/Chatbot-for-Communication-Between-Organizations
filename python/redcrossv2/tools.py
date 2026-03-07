from __future__ import annotations

import os
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from A2A_peer import A2APeer


class RedCrossA2ATools:
    def __init__(self, graph: Optional[Any], interagent_thread_id: Optional[str] = None):
        self.graph = graph
        self.interagent_thread_id = interagent_thread_id or os.getenv(
            "INTERAGENT_THREAD_ID", "hospital_redcross_case_1"
        )
        self.hospital_peer = A2APeer(
            base_url=os.getenv("HOSPITAL_BASE_URL", "http://127.0.0.1:2024")
        )
        self.send_to_hospital_a2a_tool = tool(self.send_to_hospital_a2a)
        self.visualize_interagent_state = (
            os.getenv("VISUALIZE_INTERAGENT_STATE", "true").lower() == "true"
        )

    async def _print_interagent_state(self, stage: str) -> None:
        if not self.visualize_interagent_state or self.graph is None:
            return

        config = {"configurable": {"thread_id": self.interagent_thread_id}}
        state = None
        if hasattr(self.graph, "aget_state"):
            state = await self.graph.aget_state(config)
        elif hasattr(self.graph, "get_state"):
            state = self.graph.get_state(config)

        values = getattr(state, "values", None) or {}
        messages = values.get("messages", [])
        print(
            f"\n=== REDCROSS interagent state {stage} "
            f"({self.interagent_thread_id}) ==="
        )
        if not messages:
            print("(empty)")
        else:
            for i, m in enumerate(messages, start=1):
                msg_type = getattr(m, "type", m.__class__.__name__)
                content = getattr(m, "content", str(m))
                print(f"[{i}] {msg_type}: {content}")
        print("=== end ===\n")

    async def _append_to_interagent_memory(
        self, request_text: str, response_text: str
    ) -> None:
        if self.graph is None:
            return

        config = {"configurable": {"thread_id": self.interagent_thread_id}}

        # RedCross-local memory projection:
        # red cross outbound -> AIMessage, hospital reply -> HumanMessage.
        values = {
            "messages": [
                AIMessage(
                    content=(
                        f"{request_text}"
                    )
                ),
                HumanMessage(
                    content=(
                        f"{response_text}"
                    )
                ),
            ]
        }

        if hasattr(self.graph, "aupdate_state"):
            print("Updating graph state (async)...")
            await self.graph.aupdate_state(config, values, as_node="tools")
        else:
            print("Updating graph state (sync)...")
            self.graph.update_state(config, values, as_node="tools")
        await self._print_interagent_state("AFTER update_state")

    async def send_to_hospital_a2a(self, message_text: str) -> str:
        """
        Red Cross -> Hospital (A2A)
        """
        tagged_text = (
            "SENDER: RED_CROSS_AGENT\n"
            "MODE: REQUEST_REPLY\n\n"
            f"{message_text}"
        )

        try:
            response_text, _, _ = await self.hospital_peer.send_text(
                text=tagged_text,
                context_id=self.interagent_thread_id,
                role="user",
            )
        except Exception as e:
            response_text = (
                f"[A2A_ERROR] Could not contact Hospital: {type(e).__name__}: {e}"
            )

        await self._append_to_interagent_memory(message_text, response_text)
        return response_text
