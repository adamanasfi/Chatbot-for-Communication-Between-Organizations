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

from tools import HCCA2ATools


class HCCAgent:

    ORG = "hcc"

    EMPLOYEE_PROMPT = """
    You are the Hospital Command Center (HCC) Coordination Agent.

    You support the Incident Commander in the HCC, who reviews the hospital-wide
    resource picture and approves, modifies, or denies requests coming up from
    departments such as the Emergency Department (ED), per HICS.

    This prompt is used only for the Incident Commander / HCC Agent chat and
    HCC-local database events. Do not treat these messages as ED messages.

    The Incident Commander is asking for help.
    - Help them review the current hospital-wide resource picture and any open
      requests from the ED.
    - Call send_to_er_a2a only when the Incident Commander explicitly asks to send
      an allocation decision or message to the ED, or when ED information is
      required to proceed.
    - The allocation decision (what is approved, modified, or denied) is the
      Incident Commander's judgment call, not yours to invent — carry forward
      exactly what they tell you to send.
    - After the tool call, tell the Incident Commander you contacted the ED and
      summarize their response in your own words.

    Database events are identified by: [DB EVENT] at the start of the message.
    - A hospital-wide resource changed in the database.
    - Acknowledge the change internally. Do NOT contact the ED.
    - Only contact the ED when the Incident Commander explicitly asks you to.

    Memory tools available:
    - manage_memory / search_memory for episodic, semantic, and procedural namespaces.
    - Use them according to their individual instructions.

    Security and privacy:
    - Share minimum necessary information externally.
    - Do not expose sensitive internal details unless operationally required.
    """.strip()

    INTERAGENT_PROMPT = """
    You are the Hospital Command Center (HCC) Coordination Agent.

    This prompt is used only for the ER Agent / HCC Agent A2A coordination
    channel. You are speaking to the Emergency Department agent, not the Incident
    Commander.

    - Reply to ED concisely and operationally.
    - Acknowledge what ED said, then state what HCC can confirm or will do.
    - If ED sends a situation report or resource request, acknowledge receipt and
      say that the Incident Commander will review it unless a decision is already
      available in context.
    - Keep replies to 2-3 plain sentences unless ED explicitly asks for a
      structured response.
    - Do not send another A2A message back through a tool from this channel; your
      direct response is the A2A reply.
    - Search memory when ED asks about prior Incident Commander context or earlier
      coordination history.
    - Share minimum necessary information externally.
    - Do not expose sensitive internal details unless operationally required.
    """.strip()

    _PROCEDURAL = {
        "request_review": {
            "content": (
                "Request Review: To evaluate an incoming situation report / resource request from the ED. "
                "1. Read the ED's SBAR situation report and note the Recommendation (requested resources). "
                "2. Compare the request against the current hospital-wide resource pool. "
                "3. Present the picture to the Incident Commander for a decision; do not decide unilaterally. "
                "4. Record the incoming request in episodic memory."
            )
        },
        "allocation_decision": {
            "content": (
                "Allocation Decision: To communicate the Incident Commander's decision back to the ED. "
                "1. Never invent quantities yourself — use exactly what the Incident Commander decided. "
                "2. Send the decision (approve / modify / deny) with resource type, quantity, and ETA if approved. "
                "3. Record the outcome in episodic memory."
            )
        },
        "resource_pool_tracking": {
            "content": (
                "Resource Pool Tracking: To keep the hospital-wide resource picture current. "
                "1. On any DB event affecting resources, note the change internally. "
                "2. Update semantic memory with the current hospital-wide availability by resource type."
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
            "INTERCOORD_THREAD_ID", "er_hcc_coord_case_1"
        )

        self.store = InMemoryStore(
            index={"embed": "openai:text-embedding-3-small", "dims": 1536}
        )

        for key, value in self._PROCEDURAL.items():
            self.store.put((self.ORG, "procedural"), key, value)

        episodic_manage = create_manage_memory_tool(
            namespace=(self.ORG, "episodic"),
            instructions=(
                "Record every exchange as a new entry: every message from the Incident Commander, "
                "every coordination message sent to or received from the ED, "
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
                "who the Incident Commander is, the hospital's current capacity, facts about the "
                "ED's needs and constraints, or any other operationally relevant information. "
                "Update entries when facts change. Delete entries that are no longer valid."
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
                "Fetch the correct procedure to follow: request review, "
                "allocation decision, or resource pool tracking."
            ),
            store=self.store,
        )

        self.tools_service = HCCA2ATools(
            graph=None, interagent_thread_id=self.intercoord_thread_id
        )

        shared_tools = [
            episodic_manage,
            episodic_search,
            semantic_manage,
            semantic_search,
            procedural_search,
        ]

        self.employee_graph = create_react_agent(
            model=self.llm,
            tools=[
                self.tools_service.send_to_er_a2a_tool,
                *shared_tools,
            ],
            prompt=self.EMPLOYEE_PROMPT,
            checkpointer=self.memory,
            store=self.store,
        )
        self.intercoord_graph = create_react_agent(
            model=self.llm,
            tools=shared_tools,
            prompt=self.INTERAGENT_PROMPT,
            checkpointer=self.memory,
            store=self.store,
        )
        self.graph = self.employee_graph
        self.tools_service.graph = self.intercoord_graph

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
        graph = (
            self.intercoord_graph
            if thread_id == self.intercoord_thread_id
            else self.employee_graph
        )
        response = await graph.ainvoke(inputs, config=config)
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
