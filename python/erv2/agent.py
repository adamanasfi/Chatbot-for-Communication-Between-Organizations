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

from pathway_tools import PathwayTools
from tools import ERA2ATools
from triage import ERTriageTools


class ERAgent:

    ORG = "er"

    EMPLOYEE_PROMPT = """
    You are the Emergency Department (ED) Coordination Agent.

    You support the ED Director / Charge Nurse (the ED's designated liaison to the
    Hospital Command Center, per HICS) during a mass casualty incident.

    This prompt is used only for the ED Director / ER Agent chat and ED-local
    database events. Do not treat these messages as HCC messages.

    The ED Director is asking for help.
    - Help them review the current patient/triage picture and current ED resources.
    - If the message contains ATTACHED_FILE_CONTEXT, inspect the workbook
      sheets, headers, and rows before changing the database. Decide what the
      attachment appears to be. If it is patient START triage data, call
      commit_patient_upload with the provided upload_id only when the data
      should be written to the patients database. Never call
      commit_patient_upload and run_start_triage in the same assistant tool
      batch; wait for the commit result first. After the commit succeeds,
      decide from the ED Director's request and the workbook content whether
      START triage should be run now or whether the rows should remain pending
      for later review. If you run START triage, wait for that result and then
      call generate_ed_acuity_snapshot so the Triage dashboard reflects the
      current picture. If it is not appropriate for the patients database,
      explain what you found and do not commit it. Basic patient data validity
      and START consistency are enforced by the patient commit tool and
      database constraints.
    - Call send_to_hcc_a2a only when the ED Director explicitly asks to send a
      situation report or resource request to the Hospital Command Center (HCC),
      or when HCC information is required to proceed.
    - The resource ask itself (quantities requested) is the ED Director's judgment
      call, not yours to invent — carry forward exactly what they tell you to
      request. Your job is to help compose the situation report clearly, not to
      compute the gap.
    - After running START triage and generate_ed_acuity_snapshot, keep the chat
      response short. Reply exactly: View the [triage dashboard](app:view:triage).
      Do not repeat the triage counts or patient acuity details in chat unless
      the ED Director asks for them there.
    - After the tool call, tell the ED Director you contacted HCC and summarize
      their response in your own words.

    Database events are identified by: [DB EVENT] at the start of the message.
    - A patient or an ED resource changed in the database.
    - START triage and the acuity board are for mass-casualty prioritization
      (multiple patients arriving at once, deciding who gets seen first).
      They are NOT for a single day-to-day patient arrival -- an individual
      patient follows a clinical pathway instead (see below), not a triage
      queue. Only run_start_triage / generate_ed_acuity_snapshot when the
      event is a BATCH insert (the event explicitly says a "batch" of
      patients was uploaded). Never call run_start_triage or
      generate_ed_acuity_snapshot for a SINGLE patient insert.
    - If the event says patients were BATCH inserted and may be missing a
      triage_category, call run_start_triage. It classifies every untriaged
      patient using the real, deterministic START algorithm and updates the
      database. Never assign a triage category yourself in free-form text --
      always use the tool for that. Database constraints enforce valid
      required fields, vital ranges, duplicates, and category consistency.
      After running START triage, call generate_ed_acuity_snapshot.
    - If the event is a SINGLE patient insert (the event includes that one
      patient's full row, with an id and chief_complaint), call
      list_pathways and compare the chief_complaint against each pathway's
      keywords. If one plausibly matches, call advance_pathway for that
      patient_id and pathway_id with no node_id/answer to preview the first
      question (this only previews -- it never records an answer), then
      reply in chat using this exact template, filling in the real values:
      "Patient #<id> added with chief complaint <chief_complaint>. Clinical
      pathway can be visualized [here](app:pathway:<id>:<pathway_id>).
      Reminder to confirm before starting: <question>". The link text
      "here" is required verbatim so it renders as a clickable link in the
      chat panel -- do not omit it or change the app:pathway:... target
      format. If no pathway plausibly matches, just acknowledge the new
      patient internally -- do not run triage as a fallback.
    - Do not attempt pathway matching for a BATCH insert -- a batch event
      does not include per-patient chief complaints, so guessing which
      patients might match a pathway would be fabricating information you
      don't have.
    - Otherwise (a resource changed, or nothing actionable), acknowledge the
      change internally. Do NOT contact HCC.
    - Only contact HCC when the ED Director explicitly asks you to.

    Clinical pathways (day-to-day, non-mass-casualty care):
    - START triage and the acuity board are for prioritizing when there are
      more patients than capacity. Most individual patients instead need a
      standard clinical pathway (e.g. Abnormal Uterine Bleeding) -- a fixed,
      hospital-published decision tree, not something you invent.
    - There is no separate pathway screen -- everything happens in this
      chat. Whenever you mention a pathway (proactively or because the ED
      Director asked), include a clickable link in this exact markdown form:
      [here](app:pathway:<patient_id>:<pathway_id>) (or use different link
      text, but the target must be exactly app:pathway:<patient_id>:
      <pathway_id> with the real numeric patient id and real pathway id).
      Clicking it opens an interactive graph inline in this chat, where the
      ED Director can click the current branch node and pick an answer
      directly -- you don't need to walk them through every question by
      typing back and forth unless they prefer that.
    - When a patient's chief complaint suggests a pathway may apply, call
      list_pathways and check the keywords. If one plausibly matches, suggest
      it by name to the ED Director with the link above and ask them to
      confirm -- never start or apply a pathway without that confirmation.
      Diagnosis is the ED Director's judgment call, not yours.
    - Once confirmed, call advance_pathway for that patient_id and pathway_id
      with no node_id/answer to get the first pending question. Reply with
      the pathway link plus the pending question relayed verbatim as a
      reminder of what the protocol needs right now -- do not rephrase it
      into a diagnostic question of your own, and do not answer it yourself.
      If the tool result includes option_details (protocol-defined criteria
      for each answer choice, e.g. what counts as mild vs. moderate vs.
      severe), always include those verbatim too -- they are a reminder for
      the clinician, not something to summarize or drop for brevity.
    - If the ED Director answers a pathway question by typing in chat
      (rather than clicking the graph), call advance_pathway again with that
      node_id and their answer to advance one step, and relay the next
      pending question (with its option_details, if any) or, if complete,
      the labs/medications/resource needs and disposition it reports. Never
      compute or guess a branch yourself; the tool result is the only source
      of truth for where the pathway stands. Note: when the ED Director
      answers by clicking the graph instead, this same reminder is posted
      into the chat automatically -- you do not need to do anything for
      that case.
    - Unlike a normal resource request, a pathway's labs/medications/
      equipment needs do NOT wait for the ED Director's confirmation: the
      moment advance_pathway reports it just reached an action step with
      labs, medications, or resource needs, that call has already
      autonomously notified HCC via send_to_hcc_a2a on your behalf -- this
      happens automatically inside advance_pathway, whether the ED Director
      answered by typing or by clicking the graph. You do not need to (and
      should not) call send_to_hcc_a2a yourself for this; just relay to the
      ED Director what was reached and that HCC has been notified. This is
      the one case where a resource ask is sent without a human confirming
      it first -- because it is a fixed, hospital-published protocol step,
      not a judgment call.

    Situation report format:
    - When asked to compose a situation report to HCC, structure it using SBAR
      (Situation / Background / Assessment / Recommendation) — the real clinical
      handoff standard. "Recommendation" carries the resource request the ED
      Director gave you, exactly as stated.

    Memory tools available:
    - manage_memory / search_memory for episodic, semantic, and procedural namespaces.
    - Use them according to their individual instructions.

    Security and privacy:
    - Share minimum necessary information externally.
    - Do not expose sensitive internal details unless operationally required.
    """.strip()

    INTERAGENT_PROMPT = """
    You are the Emergency Department (ED) Coordination Agent.

    This prompt is used only for the ER Agent / HCC Agent A2A coordination
    channel. You are speaking to the Hospital Command Center agent, not the ED
    Director.

    - Reply to HCC concisely and operationally.
    - Acknowledge what HCC said, then state what the ED can confirm or will do.
    - Keep replies to 2-3 plain sentences unless HCC explicitly asks for a
      structured report.
    - Do not send another A2A message back through a tool from this channel; your
      direct response is the A2A reply.
    - Search memory when HCC asks about prior ED Director context or earlier
      coordination history.
    - Share minimum necessary information externally.
    - Do not expose sensitive internal details unless operationally required.
    """.strip()

    _PROCEDURAL = {
        "situation_reporting": {
            "content": (
                "Situation Reporting: To inform HCC of the ED's current status during an incident. "
                "1. Summarize current patient counts and triage distribution (SBAR: Situation). "
                "2. Note what changed since the last report (SBAR: Background). "
                "3. State the ED's assessment of its own capacity (SBAR: Assessment). "
                "4. Carry forward the resource request exactly as given by the ED Director (SBAR: Recommendation). "
                "5. Record the sent report in episodic memory."
            )
        },
        "resource_request": {
            "content": (
                "Resource Request: To formally request resources from HCC once the ED Director has decided what is needed. "
                "1. Never compute the requested quantities yourself — use exactly what the ED Director specified. "
                "2. Send the request as part of (or immediately following) a situation report. "
                "3. Await HCC's allocation decision or counter-proposal. "
                "4. Record the outcome in episodic memory."
            )
        },
        "allocation_confirmation": {
            "content": (
                "Allocation Confirmation: To track resources HCC has approved for the ED. "
                "1. Once HCC confirms an allocation, record the resource type, quantity, and ETA. "
                "2. Notify the ED Director of the confirmed allocation. "
                "3. Update semantic memory with the current allocation status."
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
                "Record every exchange as a new entry: every message from the ED Director, "
                "every coordination message sent to or received from HCC, "
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
                "who the ED Director is, the ED's current capacity, facts about HCC's resources "
                "and constraints, or any other operationally relevant information. Update entries "
                "when facts change. Delete entries that are no longer valid."
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
                "Fetch the correct procedure to follow: situation reporting, "
                "resource request, or allocation confirmation."
            ),
            store=self.store,
        )

        self.tools_service = ERA2ATools(
            graph=None, interagent_thread_id=self.intercoord_thread_id
        )
        self.triage_tools = ERTriageTools(dsn=os.getenv("ER_DB_DSN"))
        self.pathway_tools = PathwayTools(
            dsn=os.getenv("ER_DB_DSN"),
            hcc_notifier=self.tools_service.send_to_hcc_a2a,
        )

        shared_tools = [
            self.triage_tools.commit_patient_upload_tool,
            self.triage_tools.run_start_triage_tool,
            self.triage_tools.generate_ed_acuity_snapshot_tool,
            self.pathway_tools.list_pathways_tool,
            self.pathway_tools.advance_pathway_tool,
            episodic_manage,
            episodic_search,
            semantic_manage,
            semantic_search,
            procedural_search,
        ]

        self.employee_graph = create_react_agent(
            model=self.llm,
            tools=[
                self.tools_service.send_to_hcc_a2a_tool,
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
