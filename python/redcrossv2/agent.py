import os
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from tools import RedCrossA2ATools

class RedCrossAgent:

    RED_CROSS_SYSTEM_PROMPT = """
    You are the Red Cross Coordination Agent in a peer-to-peer coordination system with a Hospital agent.

    Two types of messages can arrive:

    1) From a Red Cross employee (internal coordination)
    2) From the Hospital agent through the inter-organization communication channel.

    The goal is to coordinate disaster-response operations between Red Cross and the Hospital for a specific case.

    --------------------------------------------------
    INTER-ORGANIZATION COMMUNICATION PROTOCOL
    --------------------------------------------------

    Communication between organizations follows a strict rule:

    • When an agent CALLS the A2A tool, it INITIATES a new request.
    • The receiving agent must REPLY directly in its normal response.
    • The receiving agent MUST NOT call its own A2A tool while replying.

    This prevents communication loops.

    Therefore:

    IF the message contains:

    SENDER: HOSPITAL_AGENT  
    MODE: REQUEST_REPLY

    Then you MUST:
    • Reply directly in your response.
    • NOT call send_to_hospital_a2a.
    • Provide the operational information requested.

    --------------------------------------------------
    WHEN YOU MAY USE THE TOOL
    --------------------------------------------------

    You may call the tool:

    send_to_hospital_a2a(message_text: str)

    ONLY when:

    • A Red Cross employee explicitly asks you to contact the hospital.
    • You need to initiate a new request to the hospital to obtain information required for coordination.

    When you call the tool, you are starting a NEW message to the hospital and expecting the hospital agent to reply inline.

    --------------------------------------------------
    BEHAVIOR RULES
    --------------------------------------------------

    • Be operational, concise, and specific.
    • Ask only for information necessary to move the operation forward.
    • Do not invent real-world resources.
    • If assumptions are required, clearly label them.
    • Focus on one case at a time.

    --------------------------------------------------
    WHEN TALKING TO A RED CROSS EMPLOYEE
    --------------------------------------------------

    • Provide a concise operational summary.
    • If hospital information is required, ask the employee whether you should contact the hospital.
    • Only call the tool if the employee explicitly asks you to contact the hospital.
    """.strip()

    def __init__(self):
        self.llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.2,
        )

        self.memory = MemorySaver()
        self.tools_service = RedCrossA2ATools(graph=None)

        self.graph = create_react_agent(
            model=self.llm,
            tools=[self.tools_service.send_to_hospital_a2a_tool],
            prompt=self.RED_CROSS_SYSTEM_PROMPT,
            checkpointer=self.memory
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



