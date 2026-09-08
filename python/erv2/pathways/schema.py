from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PathwayNode:
    id: str
    kind: str  # "decision" | "action" | "terminal"
    label: str
    x: int
    y: int
    # decision nodes
    question: str | None = None
    branches: dict[str, str] = field(default_factory=dict)   # answer -> next node id
    option_details: dict[str, str] = field(default_factory=dict)  # answer -> protocol criteria text
    # action nodes (auto-advance after recording their effects)
    labs: list[str] = field(default_factory=list)
    medications: list[str] = field(default_factory=list)
    resource_needs: list[str] = field(default_factory=list)
    next_node: str | None = None
    # terminal nodes
    disposition: str | None = None


@dataclass
class Pathway:
    id: str
    name: str
    chief_complaint_keywords: list[str]
    start_node: str
    nodes: dict[str, PathwayNode]
