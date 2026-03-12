from dotenv import load_dotenv

load_dotenv()

import os
from typing import List, Optional

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from tools import RedCrossA2ATools


class RedCrossCoordinatorAgent:
    COORDINATOR_SYSTEM_PROMPT = """
    You are the Red Cross Coordinator Agent.

    You only communicate with the Hospital Coordinator Agent over the inter-organization channel.
    You do not communicate with Red Cross employees directly.

    Rules:
    - Reply with concise, operational, coordination-focused information.
    - Do not reveal private internal details unless explicitly required for coordination.
    - You may call send_to_hospital_a2a when hospital coordination is required.
    - Treat each request as inter-organization coordination only.

    Inter-organization protocol:
    - If the incoming message contains:
      SENDER: HOSPITAL_AGENT
      MODE: REQUEST_REPLY
      then you MUST reply inline in your normal response and MUST NOT call
      send_to_hospital_a2a.
    - Use send_to_hospital_a2a only when initiating a NEW outbound request.
    """.strip()

    def __init__(self):
        self.llm = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )
        self.memory = MemorySaver()
        self.handoff_thread_id = os.getenv(
            "REDCROSS_HANDOFF_THREAD_ID", "redcross_llm_to_redcross_coord_case_1"
        )
        self.intercoord_thread_id = os.getenv(
            "INTERCOORD_THREAD_ID", "hospital_redcross_coord_case_1"
        )
        self.tools_service = RedCrossA2ATools(
            graph=None, interagent_thread_id=self.intercoord_thread_id
        )

        self.graph = create_react_agent(
            model=self.llm,
            tools=[self.tools_service.send_to_hospital_a2a_tool],
            prompt=self.COORDINATOR_SYSTEM_PROMPT,
            checkpointer=self.memory,
        )
        self.tools_service.graph = self.graph

    async def run(
        self,
        user_text: Optional[str] = None,
        thread_id: Optional[str] = None,
        messages: Optional[List[BaseMessage]] = None,
    ) -> str:
        if messages is not None:
            inputs = {"messages": messages}
        else:
            inputs = {"messages": [("user", user_text or "")]}

        config = {"configurable": {"thread_id": thread_id or self.intercoord_thread_id}}
        response = await self.graph.ainvoke(inputs, config=config)

        out_messages: List[BaseMessage] = response.get("messages", [])
        if not out_messages:
            return "(no response)"
        return str(out_messages[-1].content)
