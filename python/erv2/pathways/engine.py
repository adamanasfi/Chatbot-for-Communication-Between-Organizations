from __future__ import annotations

from .schema import Pathway, PathwayNode


def run_pathway(pathway: Pathway, answers: dict[str, str]) -> dict:
    """
    Deterministically walk the pathway as far as `answers` allow.

    Never guesses a branch -- stops and reports the pending question the
    moment it hits a decision node with no recorded answer yet. This mirrors
    classify_patient's design: the coded logic owns the branching, the agent
    only relays the pending question and the final result.
    """
    visited: list[str] = []
    labs: list[str] = []
    medications: list[str] = []
    resource_needs: list[str] = []
    node_id = pathway.start_node

    while True:
        node = pathway.nodes[node_id]
        visited.append(node_id)

        if node.kind == "action":
            labs += node.labs
            medications += node.medications
            resource_needs += node.resource_needs
            if node.next_node is None:
                return _result(pathway, visited, labs, medications, resource_needs, pending=None)
            node_id = node.next_node
            continue

        if node.kind == "terminal":
            return _result(
                pathway, visited, labs, medications, resource_needs,
                pending=None, disposition=node.disposition,
            )

        # decision node
        answer = answers.get(node.id)
        if answer is None:
            return _result(pathway, visited, labs, medications, resource_needs, pending=node)
        next_id = node.branches.get(answer)
        if next_id is None:
            raise ValueError(
                f"Pathway {pathway.id!r}: no branch for answer {answer!r} at node {node.id!r} "
                f"(valid answers: {list(node.branches)})"
            )
        node_id = next_id


def _result(
    pathway: Pathway,
    visited: list[str],
    labs: list[str],
    medications: list[str],
    resource_needs: list[str],
    *,
    pending: PathwayNode | None,
    disposition: str | None = None,
) -> dict:
    return {
        "pathway_id": pathway.id,
        "visited_nodes": visited,
        "pending_node": (
            {
                "id": pending.id,
                "question": pending.question,
                "options": list(pending.branches),
                "option_details": dict(pending.option_details),
            }
            if pending else None
        ),
        "labs": labs,
        "medications": medications,
        "resource_needs": resource_needs,
        "disposition": disposition,
        "complete": pending is None,
    }
