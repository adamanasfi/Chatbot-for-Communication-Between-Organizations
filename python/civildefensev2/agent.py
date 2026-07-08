from dotenv import load_dotenv
load_dotenv()

import os
from typing import List, Optional

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from tools import CivilDefenseA2ATools


class CivilDefenseAgent:

    SYSTEM_PROMPT = """
    You are the Civil Defense Coordination Agent.

    You receive three types of messages and must handle each differently:

    1. EMPLOYEE MESSAGE
       A Civil Defense employee is asking for help.
       - Help them manage their case and coordinate resources.
       - Call send_to_red_cross_a2a only when the employee explicitly asks to contact
         Red Cross, or when Red Cross information is required to proceed.
       - Send Red Cross a concise operational request only. Do not forward the internal
         conversation.
       - After the tool call, tell the employee you contacted Red Cross and summarize
         their response in your own words.

    2. INCOMING INTER-ORGANIZATION MESSAGE
       Identified by: SENDER: RED_CROSS_AGENT / MODE: REQUEST_REPLY at the top.
       - Red Cross is reaching out to Civil Defense directly.
       - Reply to them concisely: acknowledge what they said, state what Civil Defense will do.
       - Keep the reply to 2-3 plain sentences. No lists, no headers.
       - NEVER call send_to_red_cross_a2a in response to this type of message.

    3. DATABASE EVENT
       Identified by: [DB EVENT] at the start of the message.
       - A resource in the Civil Defense database has changed.
       - Acknowledge the change internally. Do NOT contact Red Cross.
       - Only contact Red Cross when the employee explicitly asks you to.

    Security and privacy:
    - Share minimum necessary information externally.
    - Do not expose sensitive internal details unless operationally required.
    """.strip()

    def __init__(self):
        self.llm = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )
        self.memory = MemorySaver()
        self.intercoord_thread_id = os.getenv(
            "INTERCOORD_THREAD_ID", "civil_defense_redcross_coord_case_1"
        )
        self.tools_service = CivilDefenseA2ATools(
            graph=None, interagent_thread_id=self.intercoord_thread_id
        )
        self.graph = create_react_agent(
            model=self.llm,
            tools=[self.tools_service.send_to_red_cross_a2a_tool],
            prompt=self.SYSTEM_PROMPT,
            checkpointer=self.memory,
        )
        self.tools_service.graph = self.graph

    async def run(
        self,
        user_text: Optional[str] = None,
        thread_id: str = "default",
        messages: Optional[List[BaseMessage]] = None,
    ) -> str:
        if messages is not None:
            inputs = {"messages": messages}
        else:
            inputs = {"messages": [("user", user_text or "")]}
        config = {"configurable": {"thread_id": thread_id}}
        response = await self.graph.ainvoke(inputs, config=config)
        out_messages: List[BaseMessage] = response.get("messages", [])
        if not out_messages:
            return "(no response)"
        return str(out_messages[-1].content)
