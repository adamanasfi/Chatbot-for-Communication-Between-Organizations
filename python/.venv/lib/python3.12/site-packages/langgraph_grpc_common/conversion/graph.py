import logging
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from langgraph.pregel import Pregel

from langgraph_grpc_common import conversion
from langgraph_grpc_common.conversion._compat import NS_SEP, InMemoryCache, PregelNode
from langgraph_grpc_common.conversion.interrupt import static_interrupt_config_to_proto
from langgraph_grpc_common.proto import engine_common_pb2, executor_api_pb2
from langgraph_grpc_common.sanitize import encoded_str

LOGGER = logging.getLogger(__name__)


# Graph definition and checkpointer presence

_DEFAULT_NAME = sys.intern("LangGraph")


async def graph_to_proto(
    graph_id: str, graph: Pregel
) -> executor_api_pb2.GraphDefinition:
    """Extract graph information from a compiled LangGraph graph.

    Returns a protobuf message that contains all relevant orchestration information about the graph
    """
    if not graph_id:
        raise ValueError("graph_id must not be empty")
    graph_def = executor_api_pb2.GraphDefinition(
        name=graph.name if graph.name else graph_id,
        graph_id=graph_id,
        channel_specs=conversion.channel.channels_specs_to_proto(graph.channels),
        interrupt_before_nodes=static_interrupt_config_to_proto(
            graph.interrupt_before_nodes
        ),
        interrupt_after_nodes=static_interrupt_config_to_proto(
            graph.interrupt_after_nodes
        ),
        stream_mode=(
            [graph.stream_mode]
            if isinstance(graph.stream_mode, str)
            else graph.stream_mode
        ),
        stream_eager=bool(graph.stream_eager),
        stream_channels=string_or_slice_field_to_proto(graph.stream_channels),
        step_timeout=float(graph.step_timeout) if graph.step_timeout else 0.0,
        debug=bool(graph.debug),
        # TODO retry policy
        cache=executor_api_pb2.Cache(
            cache_type=_cache_type_to_proto(getattr(graph, "cache", None)),
        ),
        config=(
            conversion.config.config_to_proto(graph.config) if graph.config else None
        ),
        nodes=[
            _node_to_proto(node_name, node, graph_id)
            for node_name, node in graph.nodes.items()
        ],
        trigger_to_nodes=_nodes_to_trigger_proto(graph.nodes),
        stream_channels_asis=string_or_slice_field_to_proto(graph.stream_channels_asis),
        input_channels=string_or_slice_field_to_proto(graph.input_channels),
        output_channels=string_or_slice_field_to_proto(graph.output_channels),
        configured_tracing_name=graph.name,
    )

    return graph_def


def _node_to_proto(
    name: str, node: PregelNode, graph_id: str
) -> executor_api_pb2.NodeDefinition:
    if isinstance(node.channels, str):
        channels = [node.channels]
    elif isinstance(node.channels, list):
        channels = node.channels
    elif isinstance(node.channels, dict):
        channels = [k for k, _ in node.channels.items()]
    else:
        channels = []
    subgraph = None
    if node.subgraphs and hasattr(node.subgraphs[0], "name"):
        subgraph_name = node.subgraphs[0].name or ""
        namespace = graph_id.rsplit(NS_SEP, 1)[0] if NS_SEP in graph_id else ""
        if namespace:
            subgraph_graph_id = f"{namespace}{NS_SEP}{name}{NS_SEP}{subgraph_name}"
        else:
            subgraph_graph_id = f"{name}{NS_SEP}{subgraph_name}"
        subgraph = engine_common_pb2.Subgraph(graph_id=subgraph_graph_id)
    # TODO cache policy
    return executor_api_pb2.NodeDefinition(
        metadata_json=conversion.struct.raw_map_from_dict(node.metadata or {}),
        name=name,
        triggers=node.triggers,
        tags=node.tags,
        channels=channels,
        subgraph=subgraph,
    )


def _trigger_to_nodes_map(nodes: Mapping[str, PregelNode]) -> dict[str, list[str]]:
    """Index from a trigger to nodes that depend on it."""
    trigger_to_nodes: defaultdict[str, list[str]] = defaultdict(list)
    for name, node in nodes.items():
        for trigger in node.triggers:
            trigger_to_nodes[trigger].append(name)
    return trigger_to_nodes


def _nodes_to_trigger_proto(
    nodes_map: Mapping[str, PregelNode],
) -> dict[str, executor_api_pb2.TriggerMapping]:
    trigger_map = {}
    trigger_to_nodes = _trigger_to_nodes_map(nodes_map)
    for trigger, nodes in trigger_to_nodes.items():
        if isinstance(nodes, dict) and "nodes" in nodes:
            trigger_map[trigger] = executor_api_pb2.TriggerMapping(nodes=nodes["nodes"])
        elif isinstance(nodes, list):
            trigger_map[trigger] = executor_api_pb2.TriggerMapping(nodes=nodes)
        else:
            trigger_map[trigger] = executor_api_pb2.TriggerMapping(nodes=[])
    return trigger_map


def string_or_slice_field_to_proto(
    val: str | Sequence[str] | None,
) -> engine_common_pb2.StringOrSlice | None:
    if val is None:
        return None

    if isinstance(val, str):
        try:
            return engine_common_pb2.StringOrSlice(values=[val], is_string=True)
        except UnicodeError:
            return string_or_slice_field_to_proto(encoded_str(val))
    if isinstance(val, list):
        try:
            return engine_common_pb2.StringOrSlice(
                values=[v for v in val], is_string=False
            )
        except UnicodeError:
            return string_or_slice_field_to_proto([encoded_str(v) for v in val])
    raise NotImplementedError(f"Cannot extract field value {val} as string or slice")


def _cache_type_to_proto(cache: Any) -> str:
    """Extract cache type from a cache object."""
    if cache is None:
        return "unsupported"
    if isinstance(cache, InMemoryCache):
        return "inMemory"
    return "unsupported"
