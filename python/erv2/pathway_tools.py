from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

import asyncpg
from langchain_core.tools import tool

from pathways import PATHWAYS, Pathway, run_pathway

HccNotifier = Callable[[str], Awaitable[str]]


async def load_pathway_answers(dsn: str, patient_id: int, pathway_id: str) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT answers FROM patient_pathway_progress WHERE patient_id=$1 AND pathway_id=$2",
            patient_id, pathway_id,
        )
        return json.loads(row["answers"]) if row else {}
    finally:
        await conn.close()


async def save_pathway_answers(dsn: str, patient_id: int, pathway_id: str, answers: dict) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO patient_pathway_progress (patient_id, pathway_id, answers, updated_at) "
            "VALUES ($1, $2, $3::jsonb, now()) "
            "ON CONFLICT (patient_id, pathway_id) DO UPDATE SET answers=$3::jsonb, updated_at=now()",
            patient_id, pathway_id, json.dumps(answers),
        )
    finally:
        await conn.close()


async def advance_pathway_state(
    dsn: str,
    patient_id: int,
    pathway_id: str,
    node_id: str | None = None,
    answer: str | None = None,
    *,
    hcc_notifier: Optional[HccNotifier] = None,
) -> dict:
    """
    Core, deterministic logic shared by the agent tool and the UI's graph
    routes: validate + persist one answer (if given), run the engine, return
    the raw result dict (or {"error": str} on any validation failure).
    Performs no LLM calls -- advancing a pathway is a mechanical lookup, not
    a judgment call.

    If `hcc_notifier` is given and recording this answer newly reaches one or
    more action nodes (the pathway's labs/medications/equipment steps), it is
    awaited with a plain-text resource request -- the ER agent autonomously
    asks HCC for whatever the protocol just called for, no confirmation step.
    """
    pathway = PATHWAYS.get(pathway_id)
    if pathway is None:
        return {"error": f"Unknown pathway_id {pathway_id!r}. Known pathways: {', '.join(PATHWAYS)}"}

    answers = await load_pathway_answers(dsn, patient_id, pathway_id)
    previously_visited: set[str] = set()

    if node_id is not None and answer is not None:
        node = pathway.nodes.get(node_id)
        if node is None or node.kind != "decision":
            return {"error": f"{node_id!r} is not a decision node in pathway {pathway_id!r}."}
        if answer not in node.branches:
            valid = ", ".join(node.branches)
            return {"error": f"Invalid answer {answer!r} for node {node_id!r}. Valid answers: {valid}"}
        try:
            previously_visited = set(run_pathway(pathway, answers)["visited_nodes"])
        except ValueError:
            previously_visited = set()
        answers[node_id] = answer

    try:
        result = run_pathway(pathway, answers)
    except ValueError as exc:
        return {"error": str(exc)}

    await save_pathway_answers(dsn, patient_id, pathway_id, answers)

    recorded_new_answer = node_id is not None and answer is not None
    if hcc_notifier is not None and recorded_new_answer:
        newly_visited = [n for n in result["visited_nodes"] if n not in previously_visited]
        newly_reached_actions = [
            pathway.nodes[n] for n in newly_visited if pathway.nodes[n].kind == "action"
        ]
        if any(n.labs or n.medications or n.resource_needs for n in newly_reached_actions):
            lines = [
                f"Resource request for patient #{patient_id} ({pathway.name} pathway) "
                f"-- protocol just reached: {', '.join(n.label for n in newly_reached_actions)}."
            ]
            labs = [x for n in newly_reached_actions for x in n.labs]
            medications = [x for n in newly_reached_actions for x in n.medications]
            resource_needs = [x for n in newly_reached_actions for x in n.resource_needs]
            if labs:
                lines.append("Labs needed: " + ", ".join(labs))
            if medications:
                lines.append("Medications needed: " + ", ".join(medications))
            if resource_needs:
                lines.append("Equipment/resources needed: " + ", ".join(resource_needs))
            await hcc_notifier("\n".join(lines))

    return result


def format_pathway_result(pathway: Pathway, result: dict) -> str:
    lines = [f"Pathway: {pathway.name}", "Visited: " + " -> ".join(result["visited_nodes"])]
    if result["pending_node"]:
        pending = result["pending_node"]
        options = ", ".join(pending["options"])
        lines.append(f"NEXT STEP -- ask the ED Director: {pending['question']} (options: {options})")
        option_details = pending.get("option_details") or {}
        for option in pending["options"]:
            if option in option_details:
                lines.append(f"  - {option}: {option_details[option]}")
    else:
        if result["labs"]:
            lines.append("Labs: " + ", ".join(result["labs"]))
        if result["medications"]:
            lines.append("Medications: " + ", ".join(result["medications"]))
        if result["resource_needs"]:
            lines.append("Resource needs: " + ", ".join(result["resource_needs"]))
        if result["disposition"]:
            lines.append(f"Disposition: {result['disposition']}")
        lines.append("Pathway complete.")
    return "\n".join(lines)


def serialize_pathway_graph(pathway: Pathway) -> dict:
    """Static graph structure (nodes + edges) for rendering, independent of
    any patient's progress through it."""
    nodes = [
        {
            "id": n.id, "kind": n.kind, "label": n.label, "x": n.x, "y": n.y,
            "question": n.question, "options": list(n.branches),
            "option_details": dict(n.option_details),
            "labs": n.labs, "medications": n.medications, "resource_needs": n.resource_needs,
            "disposition": n.disposition,
        }
        for n in pathway.nodes.values()
    ]
    edges = []
    for n in pathway.nodes.values():
        if n.kind == "decision":
            for answer, target in n.branches.items():
                edges.append({"from": n.id, "to": target, "label": answer})
        elif n.kind == "action" and n.next_node:
            edges.append({"from": n.id, "to": n.next_node, "label": None})
    return {"id": pathway.id, "name": pathway.name, "nodes": nodes, "edges": edges}


class PathwayTools:
    def __init__(self, dsn: str, hcc_notifier: Optional[HccNotifier] = None):
        self.dsn = dsn
        self.hcc_notifier = hcc_notifier
        self.list_pathways_tool = tool(self.list_pathways)
        self.advance_pathway_tool = tool(self.advance_pathway)

    async def list_pathways(self) -> str:
        """
        List available clinical pathways (id, name, and the chief-complaint
        keywords they match) so you can suggest one to the ED Director based
        on a patient's chief complaint. Never apply a pathway without the ED
        Director confirming it first.
        """
        if not PATHWAYS:
            return "No pathways configured."
        return "\n".join(
            f"{p.id}: {p.name} (keywords: {', '.join(p.chief_complaint_keywords)})"
            for p in PATHWAYS.values()
        )

    async def advance_pathway(
        self,
        patient_id: int,
        pathway_id: str,
        node_id: str | None = None,
        answer: str | None = None,
    ) -> str:
        """
        Record one answer at the given decision node for a patient's clinical
        pathway progress, then report exactly where the coded pathway logic
        lands: the next pending question (relay this to the ED Director as a
        reminder of what the protocol needs now), or -- if the pathway is
        complete -- the labs/medications/resource needs the protocol calls
        for and the disposition. Never compute or guess a pathway branch
        yourself; always call this tool for that. Omit node_id and answer on
        the first call for a patient/pathway to see the first pending
        question without recording anything. Note: the ED Director may also
        be answering these questions directly on the Pathways graph in the
        UI -- call this tool with no node_id/answer at any time to see the
        current state without recording anything.
        """
        result = await advance_pathway_state(
            self.dsn, patient_id, pathway_id, node_id, answer,
            hcc_notifier=self.hcc_notifier,
        )
        if "error" in result:
            return result["error"]
        return format_pathway_result(PATHWAYS[pathway_id], result)
