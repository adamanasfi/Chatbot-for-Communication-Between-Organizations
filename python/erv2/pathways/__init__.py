from .aub import AUB_PATHWAY
from .engine import run_pathway
from .schema import Pathway, PathwayNode

PATHWAYS: dict[str, Pathway] = {
    AUB_PATHWAY.id: AUB_PATHWAY,
}

__all__ = ["Pathway", "PathwayNode", "PATHWAYS", "run_pathway"]
