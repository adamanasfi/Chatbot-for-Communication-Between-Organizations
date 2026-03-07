from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph_grpc_common import conversion
from langgraph_grpc_common.conversion.interrupt import static_interrupt_config_to_proto
from langgraph_grpc_common.proto import engine_api_pb2

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.runnables.config import RunnableConfig
    from langgraph.types import (
        All,
        Durability,
        StreamMode,
    )


def runopts_to_invoke_request(
    *,
    stream_mode: StreamMode | Sequence[StreamMode] | None,
    output_keys: str | Sequence[str] | None,
    interrupt_before: All | Sequence[str] | None,
    interrupt_after: All | Sequence[str] | None,
    durability: Durability | None,
    debug: bool | None,
    subgraphs: bool | None,
    graph_id: str,
    input: Any,
    config: RunnableConfig,
    context: Any,
) -> engine_api_pb2.InvokeRequest:
    """Convert run options to a dictionary that can be used to construct InvokeRequest or StreamRequest.

    Since proto changed to flatten RunOpts into the request messages, this now returns a dict.
    """
    stream_modes, stream_mode_single = conversion.stream_mode.stream_modes_to_proto(
        stream_mode
    )
    return engine_api_pb2.InvokeRequest(
        graph_id=graph_id,
        durability=conversion.durability.durability_to_proto(durability)
        if durability is not None
        else None,
        debug=debug,
        stream_subgraphs=subgraphs,
        stream_modes=stream_modes,
        stream_mode_single=stream_mode_single,
        langgraph_context_json=conversion.config.convert_dict_to_json_bytes(context),
        output_keys=conversion.graph.string_or_slice_field_to_proto(output_keys),
        input=conversion.value.input_to_proto(input),
        config=conversion.config.config_to_proto(config),
        interrupt_before=static_interrupt_config_to_proto(interrupt_before),
        interrupt_after=static_interrupt_config_to_proto(interrupt_after),
    )


def runopts_to_stream_request(
    *,
    stream_mode: StreamMode | Sequence[StreamMode] | None,
    output_keys: str | Sequence[str] | None,
    interrupt_before: All | Sequence[str] | None,
    interrupt_after: All | Sequence[str] | None,
    durability: Durability | None,
    debug: bool | None,
    subgraphs: bool | None,
    graph_id: str,
    input: Any,
    config: RunnableConfig,
    context: Any,
) -> engine_api_pb2.StreamRequest:
    """Convert run options to a dictionary that can be used to construct InvokeRequest or StreamRequest.

    Since proto changed to flatten RunOpts into the request messages, this now returns a dict.
    """
    stream_modes, stream_mode_single = conversion.stream_mode.stream_modes_to_proto(
        stream_mode
    )
    return engine_api_pb2.StreamRequest(
        graph_id=graph_id,
        durability=conversion.durability.durability_to_proto(durability)
        if durability is not None
        else None,
        debug=debug,
        stream_subgraphs=subgraphs,
        stream_modes=stream_modes,
        stream_mode_single=stream_mode_single,
        output_keys=conversion.graph.string_or_slice_field_to_proto(output_keys),
        input=conversion.value.input_to_proto(input),
        config=conversion.config.config_to_proto(config),
        interrupt_before=static_interrupt_config_to_proto(interrupt_before),
        interrupt_after=static_interrupt_config_to_proto(interrupt_after),
        langgraph_context_json=conversion.config.convert_dict_to_json_bytes(context),
    )
