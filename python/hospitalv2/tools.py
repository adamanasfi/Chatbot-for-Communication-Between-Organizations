from __future__ import annotations

import os
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from A2A_peer import A2APeer


class HospitalA2ATools:
    def __init__(self, graph: Optional[Any], interagent_thread_id: Optional[str] = None):
        self.graph = graph
        self.interagent_thread_id = interagent_thread_id or os.getenv(
            "INTERAGENT_THREAD_ID", "hospital_redcross_case_1"
        )
        self.red_cross_peer = A2APeer(
            base_url=os.getenv("RED_CROSS_BASE_URL", "http://127.0.0.1:2025")
        )
        self.send_to_red_cross_a2a_tool = tool(self.send_to_red_cross_a2a)
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
            f"\n=== HOSPITAL interagent state {stage} "
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

        # Hospital-local memory projection:
        # hospital outbound -> AIMessage, red cross reply -> HumanMessage.
        values = {
            "messages": [
                AIMessage(
                    content=(
                        # "SENDER: HOSPITAL_AGENT\n"
                        # "MODE: REQUEST_INIT\n\n"
                        f"{request_text}"
                    )
                ),
                HumanMessage(
                    content=(
                        # "SENDER: RED_CROSS_AGENT\n"
                        # "MODE: REQUEST_REPLY\n\n"
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

    async def send_to_red_cross_a2a(self, request_text: str) -> str:
        """
        Hospital -> Red Cross (A2A)
        """
        tagged_text = (
            "SENDER: HOSPITAL_AGENT\n"
            "MODE: REQUEST_REPLY\n\n"
            f"{request_text}"
        )

        try:
            response_text, _, _ = await self.red_cross_peer.send_text(
                text=tagged_text,
                context_id=self.interagent_thread_id,
                role="user",
            )
        except Exception as e:
            response_text = (
                f"[A2A_ERROR] Could not contact Red Cross: {type(e).__name__}: {e}"
            )

        await self._append_to_interagent_memory(request_text, response_text)
        return response_text
