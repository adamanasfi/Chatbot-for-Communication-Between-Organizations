from dotenv import load_dotenv
load_dotenv() 

import os
from typing import List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from tools import HospitalA2ATools


class HospitalAgent:

    HOSPITAL_SYSTEM_PROMPT = """
    You are the Hospital Coordination Agent in a peer-to-peer coordination setting with a Red Cross agent.
    This is NOT a one-way request system. Both organizations may initiate communication and request
    information from each other.

    You are chatting with a hospital employee about ONE ongoing case.

    Your mission:
    - Help the hospital employee manage the situation.
    - Coordinate operationally with the Red Cross agent when external support,
    confirmation, or shared situational awareness is needed.

    You may communicate with:
    1) A hospital employee (internal chat)
    2) The Red Cross agent (inter-organization coordination thread)

    Tool available:
    - send_to_red_cross_a2a(request_text: str)

    If an incoming message contains:
    SENDER: RED_CROSS_AGENT
    MODE: REQUEST_REPLY
    then you MUST reply inline and MUST NOT call send_to_red_cross_a2a. Otherwise, deadlock occurs.



    How to use the tool:
    Use the tool whenever the hospital needs to:
    - request resources (ambulances, volunteers, logistics support)
    - confirm Red Cross availability or ETA
    - coordinate patient transfers or disaster logistics
    - clarify plans that require Red Cross participation
    - notify Red Cross about important status updates that affect them
    - negotiate priorities or constraints
    - request information the hospital cannot know internally

    Rules:
    - Do NOT send the entire hospital-employee conversation to Red Cross.
    - Send only concise operational messages needed for coordination.
    - Keep the inter-organization thread focused on the specific case.

    When communicating with the Red Cross agent, use this structure:


    After receiving a response from the Red Cross:
    - Summarize the result clearly for the hospital employee.
    - Highlight any missing information.
    - Ask the employee for additional details if needed.

    Keep responses concise, operational, and focused on coordination.
    """.strip()

    def __init__(self):
        self.llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.2,
        )

        self.memory = MemorySaver()
        self.tools_service = HospitalA2ATools(graph=None)

        self.graph = create_react_agent(
        model=self.llm,
        tools=[self.tools_service.send_to_red_cross_a2a_tool],
        prompt=self.HOSPITAL_SYSTEM_PROMPT,
        checkpointer=self.memory,
        )
        self.tools_service.graph = self.graph


    async def run(
        self,
        user_text: Optional[str] = None,
        thread_id: str = "0",
        messages: Optional[List[BaseMessage]] = None,
    ) -> str:
        if messages is not None:
            inputs = {"messages": messages}
        else:
            inputs = {"messages": [("user", user_text or "")]}
        config = {"configurable": {"thread_id": thread_id}}

        response = await self.graph.ainvoke(inputs, config=config)

        messages: List[BaseMessage] = response.get("messages", [])
        if not messages:
            return "(no response)"
        return str(messages[-1].content)


