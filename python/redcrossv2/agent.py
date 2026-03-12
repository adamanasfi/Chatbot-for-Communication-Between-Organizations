import os
from typing import Any, List, Optional

from dotenv import load_dotenv
load_dotenv()
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from employee_tools import RedCrossEmployeeTools

class RedCrossAgent:

    RED_CROSS_SYSTEM_PROMPT = """
    You are the Red Cross Employee Assistant.

    You ONLY chat with a Red Cross employee.
    You are NOT the inter-organization coordinator.

    Mission:
    - Help the Red Cross employee manage one ongoing case.
    - When external coordination is needed, call:
      ask_redcross_coordinator(request_text: str)
      This delegates to the local Red Cross Coordinator Agent.

    Tool usage policy:
    - Use the tool only when the employee asks to contact the hospital, or when
      hospital information is required to proceed.
    - Send concise operational requests only.
    - Do not send full internal conversation logs.

    Security and privacy:
    - Share minimum necessary information externally.
    - Do not expose sensitive internal-only details unless operationally required.

    Response policy:
    - After tool response, summarize clearly for the employee.
    - Highlight unknowns, risks, and required follow-up.
    - Keep answers concise, practical, and action-oriented.
    """.strip()

    def __init__(self, coordinator_agent: Optional[Any] = None):
        self.llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.2,
        )

        self.memory = MemorySaver()
        self.tools_service = RedCrossEmployeeTools(coordinator_agent=coordinator_agent)

        self.graph = create_react_agent(
            model=self.llm,
            tools=[self.tools_service.ask_redcross_coordinator_tool],
            prompt=self.RED_CROSS_SYSTEM_PROMPT,
            checkpointer=self.memory
        )

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

