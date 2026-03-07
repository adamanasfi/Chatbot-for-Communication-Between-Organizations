import base64
import builtins
import copy
import logging
from collections.abc import Sequence
from typing import Any

import orjson
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt, GraphRecursionError, ParentCommand
from langgraph.types import Interrupt, PregelTask, StateSnapshot, StreamMode

from langgraph_grpc_common import conversion, serde
from langgraph_grpc_common.conversion._compat import (
    CONFIG_KEY_CHECKPOINT_ID,
    CONFIG_KEY_CHECKPOINT_MAP,
    CONFIG_KEY_CHECKPOINT_NS,
    CONFIG_KEY_THREAD_ID,
)
from langgraph_grpc_common.conversion.config import config_from_proto_optional
from langgraph_grpc_common.proto import engine_api_pb2, engine_common_pb2, errors_pb2

logger = logging.getLogger(__name__)

SERIALIZED_VALUE_KEYS = {"encoding", "value"}


def deser_vals(chunk: dict[str, Any]):
    return _deser_vals(copy.deepcopy(chunk))


def _deser_vals(current_chunk: Any):
    if isinstance(current_chunk, list):
        return [_deser_vals(v) for v in current_chunk]
    if not isinstance(current_chunk, dict):
        return current_chunk
    if current_chunk.keys() == SERIALIZED_VALUE_KEYS:
        return serde.get_serializer().loads_typed(
            (current_chunk["encoding"], base64.b64decode(current_chunk["value"]))
        )
    for k, v in current_chunk.items():
        if isinstance(v, dict | Sequence):
            current_chunk[k] = _deser_vals(v)
    return current_chunk


def state_snapshot_from_proto(
    state_pb: engine_common_pb2.StateSnapshot,
) -> StateSnapshot:
    return StateSnapshot(
        values=(
            deser_vals(orjson.loads(state_pb.values_json))
            if state_pb.values_json
            else {}
        ),
        next=tuple(state_pb.next),
        config=conversion.config.config_from_proto(state_pb.config),
        metadata=conversion.checkpoint.checkpoint_metadata_from_proto(
            state_pb.metadata
        ),
        created_at=state_pb.created_at,
        parent_config=(
            config_from_proto_optional(state_pb.parent_config)
            if state_pb.HasField("parent_config")
            else None
        ),
        tasks=tuple([pregel_task_from_proto(task_pb) for task_pb in state_pb.tasks]),
        interrupts=tuple(
            [
                conversion.value.serialized_value_from_proto(i_pb.value)
                for i_pb in state_pb.interrupts
            ]
        ),
    )


def checkpoint_ref_to_runnable_config(
    ref_pb: engine_common_pb2.CheckpointRef | None,
):
    """Convert a CheckpointRef proto to a RunnableConfig."""
    if not ref_pb:
        return None
    configurable = {}
    if ref_pb.thread_id:
        configurable[CONFIG_KEY_THREAD_ID] = ref_pb.thread_id
    if ref_pb.checkpoint_ns:
        configurable[CONFIG_KEY_CHECKPOINT_NS] = ref_pb.checkpoint_ns
    if ref_pb.checkpoint_id:
        configurable[CONFIG_KEY_CHECKPOINT_ID] = ref_pb.checkpoint_id
    if ref_pb.checkpoint_map:
        configurable[CONFIG_KEY_CHECKPOINT_MAP] = dict(ref_pb.checkpoint_map)

    return RunnableConfig(configurable=configurable) if configurable else None


def pregel_task_from_proto(
    task_pb: engine_common_pb2.PregelTaskSnapshot,
) -> PregelTask:
    which_state = task_pb.WhichOneof("state")
    if which_state == "state_snapshot":
        # subgraphs=True: state is the complete nested subgraph state
        state = state_snapshot_from_proto(task_pb.state_snapshot)
    elif which_state == "checkpoint_ref":
        # subgraphs=False: state is a reference to a subgraph checkpoint
        state = checkpoint_ref_to_runnable_config(task_pb.checkpoint_ref)
    else:
        state = None

    return PregelTask(
        id=task_pb.id,
        name=task_pb.name,
        path=conversion.task.ensure_task_path(task_pb.path),
        error=(
            serde.get_serializer().loads_typed(
                (task_pb.error.encoding, task_pb.error.value)
            )
            if task_pb.error is not None and task_pb.error.encoding
            else None
        ),
        interrupts=(
            tuple(
                [
                    conversion.value.serialized_value_from_proto(i_pb.value)
                    for i_pb in task_pb.interrupts
                ]
            )
            if task_pb.interrupts
            else tuple()
        ),
        state=state,
        result=(
            (orjson.loads(task_pb.result_json) or None) if task_pb.result_json else None
        ),
    )


def decode_state_history_response(response: engine_api_pb2.GetStateHistoryResponse):
    if not response:
        return

    return [state_snapshot_from_proto(state_pb) for state_pb in response.history]


def decode_state_response(response: engine_api_pb2.GetStateResponse):
    if not response:
        return

    return state_snapshot_from_proto(response.state)


def parse_graph_interrupt(
    graph_interrupt: engine_common_pb2.GraphInterrupt,
) -> Exception:
    """Parse a GraphInterrupt proto into a Python GraphInterrupt exception.

    Args:
        graph_interrupt: The GraphInterrupt proto message

    Returns:
        GraphInterrupt exception
    """
    # Static interrupt (interrupt_before or interrupt_after)
    if len(graph_interrupt.interrupts) == 1 and graph_interrupt.interrupts[0].id == "":
        raise GraphInterrupt()

    interrupts = []
    for interrupt in graph_interrupt.interrupts:
        interrupts.append(
            Interrupt(
                value=serde.get_serializer().loads_typed(
                    (
                        interrupt.value.encoding,
                        interrupt.value.value,
                    )
                ),
                id=interrupt.id,
            )
        )
    raise GraphInterrupt(interrupts)


def parse_user_code_execution_error(
    e: errors_pb2.UserCodeExecutionError,
) -> Exception:
    """Parse a structured user code execution error proto into a Python exception.

    Preserves all error information from the proto including error_type, error_message, and traceback.

    Args:
        e: The UserCodeExecutionError proto message containing:
           - error_type: The type/class name of the exception
           - error_message: The error message
           - traceback: The full traceback string

    Returns:
        A Python exception with all error information preserved
    """
    error_type = e.error_type or "Exception"
    error_message = e.error_message or "Unknown error"
    traceback_str = e.traceback or ""

    # Build a comprehensive error message
    full_message = error_message
    if traceback_str:
        full_message = f"{full_message}\n\nTraceback:\n{traceback_str}"

    # Try to dynamically create the exception type if it's a known Python exception
    try:
        # Try to get the exception class from builtins
        exc_class = getattr(builtins, error_type, None)
        if (
            exc_class
            and isinstance(exc_class, type)
            and issubclass(exc_class, BaseException)
        ):
            return exc_class(full_message)
    except (AttributeError, TypeError):
        pass

    # Fallback to a generic Exception with detailed message including type
    return Exception(f"{error_type}: {full_message}")


def decode_invoke_response(
    response: engine_api_pb2.InvokeResponse,
    stream_mode: StreamMode,
) -> Any:
    """Decode InvokeResponse from the strongly-typed invoke API.

    Args:
        response: The InvokeResponse proto
        stream_mode: The stream mode used for the invoke call

    Returns:
        The decoded response value

    Raises:
        Exception: If the response contains an error
    """
    which = response.WhichOneof("value")

    if which == "user_error":
        raise parse_user_code_execution_error(response.user_error)

    if which == "unsuppressed_interrupt_error":
        raise parse_graph_interrupt(response.unsuppressed_interrupt_error)

    if which == "empty":
        return None

    if which == "single_output":
        return _deserialize_value_recursively(response.single_output)

    if which == "chunk_list":
        # Return list of decoded chunks (each chunk is a ResponseChunk)
        # TODO: we don't have any test cases for this yet
        return [
            decode_response_chunk(chunk, stream_mode)
            for chunk in response.chunk_list.responses
        ]

    if which == "recursion_limit":
        limit = response.recursion_limit.limit
        if limit:
            raise GraphRecursionError(
                f"Recursion limit of {limit} reached without hitting a stop"
            )
        raise GraphRecursionError("Recursion limit exceeded")

    if which == "parent_command":
        cmd = conversion.value.command_from_proto(response.parent_command.command)
        raise ParentCommand(cmd)

    raise ValueError(f"Unknown InvokeResponse type: {which}")


def decode_stream_output_chunk(
    response: engine_api_pb2.StreamOutputChunk,
    stream_mode: StreamMode | Sequence[StreamMode],
    subgraphs: bool | None = None,
):
    """Decode a StreamOutputChunk from the engine API.

    StreamOutputChunk can contain either:
    - chunk: ResponseChunk with namespaces, mode, and SerializedValue payload
    - user_code_error: UserCodeExecutionError from the executor during run execution
    - chat_message: ChatMessageEnvelope for chat streaming
    - unsuppressed_interrupt_error: GraphInterrupt from a nested subgraph
    """
    which = response.WhichOneof("message")

    if which == "user_error":
        raise parse_user_code_execution_error(response.user_error)

    if which == "chunk":
        return decode_response_chunk(response.chunk, stream_mode, subgraphs=subgraphs)

    if which == "chat_message":
        ns = tuple(response.chat_message.namespace)
        data = conversion.messages.chat_message_tuple_from_proto(response.chat_message)
        mode = "messages"

        # TODO: document this behavior better
        is_list = isinstance(stream_mode, list)
        if is_list and subgraphs:
            return (ns, mode, data)
        if is_list:
            return (mode, data)
        if subgraphs:
            return (ns, data)
        return data

    if which == "recursion_limit":
        limit = response.recursion_limit.limit
        if limit:
            raise GraphRecursionError(
                f"Recursion limit of {limit} reached without hitting a stop"
            )
        raise GraphRecursionError("Recursion limit exceeded")

    if which == "unsuppressed_interrupt_error":
        raise parse_graph_interrupt(response.unsuppressed_interrupt_error)

    raise ValueError(f"Unknown StreamOutputChunk message type: {which}")


def decode_response_chunk(
    chunk: engine_common_pb2.ResponseChunk,
    stream_mode: StreamMode | Sequence[StreamMode],
    subgraphs: bool | None = None,
):
    """Decode a ResponseChunk from the engine.

    ResponseChunk contains:
    - namespaces: list of namespace strings
    - mode: stream mode string
    - payload: SerializedValue that needs deserialization
    """
    # Get namespaces (note: it's 'namespaces' plural in ResponseChunk)
    ns = tuple(chunk.namespaces) if chunk.namespaces else ()
    mode = chunk.mode if chunk.mode else None

    # Deserialize the payload (SerializedValue)
    payload = _deserialize_value_recursively(chunk.payload) if chunk.payload else None

    # Include ns tuple when subgraphs=True or ns is non-empty
    include_ns = subgraphs or ns
    if include_ns:
        if mode:
            return (ns, mode, payload)
        return (ns, payload)
    if mode:
        return (mode, payload)

    return payload


def _deserialize_value_recursively(value: engine_common_pb2.SerializedValue):
    """
    Deserialize a value recursively. After deserializing, if the result is a dict or list,
    deserialize the nested values recursively by checking if the item is SerializedValue.
    If it is, deserialize it recursively.

    Args:
        value: The SerializedValue proto message

    Returns:
        The deserialized value
    """
    # First, deserialize the top-level value
    deserialized = serde.get_serializer().loads_typed((value.encoding, value.value))

    # Recursively process nested structures
    return _process_deserialized_value(deserialized)


def _process_deserialized_value(value: Any) -> Any:
    """
    Recursively process a deserialized value, checking for nested SerializedValue protos.

    Args:
        value: The value to process (could be dict, list, SerializedValue proto, or primitive)

    Returns:
        The fully deserialized value with all nested SerializedValue protos resolved
    """
    # Check if this is a SerializedValue proto that needs to be deserialized
    if isinstance(value, engine_common_pb2.SerializedValue):
        # Recursively deserialize this SerializedValue proto
        return _deserialize_value_recursively(value)

    # Check if this is a dict representation of a SerializedValue (already deserialized from proto)
    if isinstance(value, dict) and value.keys() == SERIALIZED_VALUE_KEYS:
        # This is a nested SerializedValue that needs to be deserialized
        # Deserialize and recursively process
        deserialized = serde.get_serializer().loads_typed(
            (value["encoding"], value["value"])
        )
        return _process_deserialized_value(deserialized)

    # If it's a dict, recursively process all values and coerce triggers/interrupts to tuple
    if isinstance(value, dict):
        processed = {k: _process_deserialized_value(v) for k, v in value.items()}
        # For OSS consistency
        if "triggers" in processed and isinstance(processed["triggers"], list):
            processed["triggers"] = tuple(processed["triggers"])
        if "__interrupt__" in processed and isinstance(
            processed["__interrupt__"], list
        ):
            processed["__interrupt__"] = tuple(processed["__interrupt__"])
        return processed

    # If it's a list, recursively process all items
    if isinstance(value, list):
        return [_process_deserialized_value(item) for item in value]

    # For primitives and other types, return as-is
    return value
