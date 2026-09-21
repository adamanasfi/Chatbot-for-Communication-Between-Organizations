from dotenv import load_dotenv
load_dotenv()

import os
import uuid
from typing import List, Optional

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.store.memory import InMemoryStore
from langmem import create_manage_memory_tool, create_search_memory_tool

from tools import CivilDefenseA2ATools
from vehicle_tracking_tools import VehicleTrackingTools


class CivilDefenseAgent:

    ORG = "civil_defense"

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

    Partner vehicle tracking:
    - list_tracked_vehicles looks up the latest known position of partner
      orgs' vehicles (e.g. Red Cross ambulances) from a table Red Cross
      pushes to directly -- this is a live lookup, not something you poll
      on your own. Call it only when the employee actually asks where a
      partner vehicle is right now.

    Memory tools available:
    - manage_memory / search_memory for episodic, semantic, and procedural namespaces.
    - Use them according to their individual instructions.

    Security and privacy:
    - Share minimum necessary information externally.
    - Do not expose sensitive internal details unless operationally required.
    """.strip()

    _PROCEDURAL = {
        "resource_discovery": {
            "content": (
                "Resource Discovery: To find out what resources the other organization has available. "
                "1. Send a resource availability inquiry specifying the resource type and quantity needed. "
                "2. Wait for their confirmation of current availability and any constraints. "
                "3. Record the confirmed availability in semantic memory."
            )
        },
        "resource_ordering": {
            "content": (
                "Resource Ordering: To formally request a resource from the other organization. "
                "1. Confirm availability first through resource discovery. "
                "2. Send a formal request including: resource type, quantity, delivery location, required time. "
                "3. Await confirmation or counter-proposal. "
                "4. Record the outcome in episodic memory."
            )
        },
        "resource_deployment": {
            "content": (
                "Resource Deployment: To confirm and track resource deployment. "
                "1. Once the other organization confirms dispatch, record the expected arrival time and location. "
                "2. Notify the employee of the confirmed deployment details. "
                "3. Update semantic memory with the current deployment status."
            )
        },
    }

    def __init__(self):
        self.llm = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.2,
        )
        self.memory = MemorySaver()
        self.intercoord_thread_id = os.getenv(
            "INTERCOORD_THREAD_ID", "civil_defense_redcross_coord_case_1"
        )

        self.store = InMemoryStore(
            index={"embed": "openai:text-embedding-3-small", "dims": 1536}
        )

        for key, value in self._PROCEDURAL.items():
            self.store.put((self.ORG, "procedural"), key, value)

        episodic_manage = create_manage_memory_tool(
            namespace=(self.ORG, "episodic"),
            instructions=(
                "Record every exchange as a new entry: every message from the employee, "
                "every coordination message sent to or received from the other organization, "
                "and every DB event. Always create, never update or delete."
            ),
            actions_permitted=("create",),
            store=self.store,
        )
        episodic_search = create_search_memory_tool(
            namespace=(self.ORG, "episodic"),
            instructions="Search to load past episodes into the context window.",
            store=self.store,
        )
        semantic_manage = create_manage_memory_tool(
            namespace=(self.ORG, "semantic"),
            instructions=(
                "Proactively call this tool whenever you learn a new fact during conversation: "
                "who the employee is, their role, their responsibilities, facts about the other "
                "organization's resources and constraints, or any other operationally relevant "
                "information. Update entries when facts change. Delete entries that are no longer valid."
            ),
            actions_permitted=("create", "update", "delete"),
            store=self.store,
        )
        semantic_search = create_search_memory_tool(
            namespace=(self.ORG, "semantic"),
            instructions="Recall relevant known facts.",
            store=self.store,
        )
        procedural_search = create_search_memory_tool(
            namespace=(self.ORG, "procedural"),
            instructions=(
                "Fetch the correct procedure to follow: resource discovery, "
                "resource ordering, or resource deployment."
            ),
            store=self.store,
        )

        self.tools_service = CivilDefenseA2ATools(
            graph=None, interagent_thread_id=self.intercoord_thread_id
        )
        self.vehicle_tracking_tools = VehicleTrackingTools(dsn=os.getenv("CIVILDEFENSE_DB_DSN"))

        self.graph = create_react_agent(
            model=self.llm,
            tools=[
                self.tools_service.send_to_red_cross_a2a_tool,
                self.vehicle_tracking_tools.list_tracked_vehicles_tool,
                episodic_manage,
                episodic_search,
                semantic_manage,
                semantic_search,
                procedural_search,
            ],
            prompt=self.SYSTEM_PROMPT,
            checkpointer=self.memory,
            store=self.store,
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
            log_input = messages[0].content if messages else ""
        else:
            inputs = {"messages": [("user", user_text or "")]}
            log_input = user_text or ""
        config = {"configurable": {"thread_id": thread_id}}
        response = await self.graph.ainvoke(inputs, config=config)
        out_messages: List[BaseMessage] = response.get("messages", [])
        if not out_messages:
            return "(no response)"
        reply = str(out_messages[-1].content)
        self.store.put(
            (self.ORG, "episodic"),
            str(uuid.uuid4()),
            {"content": f"Input: {log_input}\nReply: {reply}"},
        )
        return reply
