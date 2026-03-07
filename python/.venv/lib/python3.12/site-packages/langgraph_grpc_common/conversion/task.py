from collections import deque
from collections.abc import Sequence
from collections.abc import Sequence as SequenceType
from dataclasses import is_dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from langchain_core.runnables.config import RunnableConfig
from langgraph.channels.base import BaseChannel
from langgraph.managed.base import ManagedValueMapping
from langgraph.pregel import Pregel
from langgraph.store.base import BaseStore
from langgraph.types import CacheKey, PregelExecutableTask
from pydantic import BaseModel
from xxhash import xxh3_128_hexdigest

from langgraph_grpc_common.conversion._compat import (
    CACHE_NS_WRITES,
    CONF,
    CONFIG_KEY_CHECKPOINT_NS,
    CONFIG_KEY_READ,
    CONFIG_KEY_RESUME_MAP,
    CONFIG_KEY_RUNTIME,
    CONFIG_KEY_SCRATCHPAD,
    CONFIG_KEY_SEND,
    DEFAULT_RUNTIME,
    PULL,
    PUSH,
    TASKS,
    PregelNode,
    PregelScratchpad,
    PregelTaskWrites,
    Runtime,
    _proc_input,
    _scratchpad,
    ensure_config,
    identifier,
    local_read,
    patch_config,
)
from langgraph_grpc_common.conversion.checkpoint import pending_writes_from_proto
from langgraph_grpc_common.conversion.config import config_from_proto
from langgraph_grpc_common.conversion.value import value_from_proto, value_to_proto
from langgraph_grpc_common.proto import engine_common_pb2
from langgraph_grpc_common.sanitize import encoded_str

if TYPE_CHECKING:
    from langgraph.checkpoint.base import PendingWrite


def task_writes_from_proto(
    tasks_proto: SequenceType[engine_common_pb2.Task],
) -> SequenceType[PregelTaskWrites]:
    return [
        PregelTaskWrites(
            ensure_task_path(t.task_path),
            t.name,
            [(w.channel, value_from_proto(w.value)) for w in t.writes],
            t.triggers,
        )
        for t in tasks_proto
    ]


def task_writes_to_proto(
    writes: Sequence[tuple[str, Any]],
) -> SequenceType[engine_common_pb2.Write]:
    writes_proto = []
    for channel, val in writes:
        val_pb = value_to_proto(channel, val)
        channel_write = engine_common_pb2.Write(
            channel=encoded_str(channel), value=val_pb
        )
        writes_proto.append(channel_write)
    return writes_proto


def ensure_task_path(
    task_path: SequenceType[engine_common_pb2.PathSegment],
) -> tuple[str | int, ...]:
    return tuple(path_segment_to_primitive(path_segment) for path_segment in task_path)


def path_segment_to_primitive(
    path_segment: engine_common_pb2.PathSegment,
) -> str | int | bool:
    if path_segment.HasField("string_value"):
        return path_segment.string_value
    if path_segment.HasField("int_value"):
        return path_segment.int_value
    if path_segment.HasField("bool_value"):
        return path_segment.bool_value
    raise ValueError(f"Invalid path segment: {path_segment}")


def pregel_executable_task_from_proto(
    task_proto: engine_common_pb2.Task,
    step: int,
    stop: int,
    channels: dict[str, BaseChannel],
    managed: ManagedValueMapping,
    graph: Pregel,
    proc: PregelNode,
    *,
    store: BaseStore | None = None,
    config: RunnableConfig | None = None,
    custom_stream_writer=None,
    clear_after_read: bool = False,
) -> PregelExecutableTask:
    """Convert a Task proto to a PregelExecutableTask.

    Args:
        task_proto: The task proto to convert.
        step: Current step number.
        stop: Stop step number.
        channels: Channel mapping.
        managed: Managed value mapping.
        graph: The Pregel graph.
        proc: The PregelNode processor.
        store: Optional store.
        config: Optional config (if not provided, extracted from task_proto).
        custom_stream_writer: Optional custom stream writer.
        clear_after_read: If True, clear large proto fields after extraction
            to free memory. This avoids holding both serialized and deserialized
            forms in memory simultaneously.

    Returns:
        The executable task.
    """
    task_path = ensure_task_path(task_proto.task_path)
    try:
        if config is None:
            config = config_from_proto(task_proto.config)
        config = ensure_config(config)
        configurable = (config or {}).get(CONF, {})

        scratchpad = scratchpad_from_proto(config, step, stop, task_proto=task_proto)

        # Clear pending_writes after extraction since they can be large
        if clear_after_read and task_proto.pending_writes:
            del task_proto.pending_writes[:]

        if task_path[0] == PULL:
            val = _proc_input(
                proc,
                managed,
                channels,
                for_execution=True,
                scratchpad=scratchpad,
                input_cache=None,
            )
        elif task_path[0] == PUSH:
            tasks_channel = channels.get(TASKS)
            if tasks_channel is None:
                raise ValueError(f"TASKS channel not found for PUSH task: {task_path}")
            sends = tasks_channel.get()
            send_idx = task_path[1]
            val = sends[send_idx].arg
        else:
            raise ValueError(f"Invalid task path: {task_path}")

        writes = deque()
        runtime = ensure_runtime(
            configurable, store, graph, custom_stream_writer=custom_stream_writer
        )

        # Generate cache key if cache policy exists
        cache_policy = getattr(proc, "cache_policy", None)
        cache_key = None
        if cache_policy:
            args_key = cache_policy.key_func(
                *([val] if not isinstance(val, list | tuple) else val),
            )
            cache_key = CacheKey(
                (CACHE_NS_WRITES, identifier(proc.node) or "__dynamic__"),
                xxh3_128_hexdigest(
                    args_key.encode() if isinstance(args_key, str) else args_key,
                ),
                cache_policy.ttl,
            )

        node = proc.node
        if node is None:
            raise ValueError(f"Node {task_proto.name} has no bound runnable or writers")

        task = PregelExecutableTask(
            name=task_proto.name,
            input=val,
            proc=node,
            writes=writes,
            config=patch_config(
                config,
                configurable={
                    CONFIG_KEY_SEND: writes.extend,
                    CONFIG_KEY_READ: partial(
                        local_read,
                        scratchpad,
                        channels,
                        managed,
                        PregelTaskWrites(
                            task_path[:3],
                            task_proto.name,
                            writes,
                            task_proto.triggers,
                        ),
                    ),
                    CONFIG_KEY_RUNTIME: runtime,
                    CONFIG_KEY_SCRATCHPAD: scratchpad,
                },
            ),
            triggers=task_proto.triggers,
            id=task_proto.id,
            path=task_path,
            retry_policy=proc.retry_policy or graph.retry_policy or [],
            cache_key=cache_key,  # TODO support
            writers=proc.flat_writers,
            subgraphs=proc.subgraphs,
        )
    except Exception as e:
        raise e

    return task


def scratchpad_from_proto(
    config: RunnableConfig,
    step: int,
    stop: int,
    task_proto: engine_common_pb2.Task | None = None,
) -> PregelScratchpad:
    # TODO: We shouldn't be accepting null tasks here actually
    configurable = config.setdefault(CONF, {})
    task_checkpoint_ns: str = configurable.get(CONFIG_KEY_CHECKPOINT_NS) or ""
    resume_map = configurable.get(CONFIG_KEY_RESUME_MAP)
    if task_proto is not None:
        task_id = task_proto.id
        pending_writes: list[PendingWrite] = (
            (pending_writes_from_proto(task_proto.pending_writes) or [])
            if (
                hasattr(task_proto, "pending_writes")
                and len(task_proto.pending_writes) > 0
            )
            else []
        )
    else:
        task_id = ""
        pending_writes = []

    scratchpad = _scratchpad(
        configurable.get(CONFIG_KEY_SCRATCHPAD),
        pending_writes,
        task_id,
        xxh3_128_hexdigest(task_checkpoint_ns.encode()),
        resume_map,
        step,
        stop,
    )

    return scratchpad


def ensure_runtime(
    configurable: dict[str, Any],
    store: BaseStore | None,
    graph: Pregel,
    custom_stream_writer=None,
) -> Runtime:
    runtime = configurable.get(CONFIG_KEY_RUNTIME)

    # Prepare runtime overrides
    overrides = {"store": store}
    if custom_stream_writer is not None:
        overrides["stream_writer"] = custom_stream_writer

    if runtime is None:
        return DEFAULT_RUNTIME.override(**overrides)
    if isinstance(runtime, Runtime):
        return runtime.override(**overrides)
    if isinstance(runtime, dict):
        context = _coerce_context(graph, runtime.get("context"))
        return Runtime(
            **(
                runtime
                | {"store": store, "context": context}
                | (
                    {"stream_writer": custom_stream_writer}
                    if custom_stream_writer
                    else {}
                )
            )
        )
    raise ValueError("Invalid runtime")


def _coerce_context(graph: Pregel, context: Any) -> Any:
    if context is None:
        return None

    context_schema = graph.context_schema
    if context_schema is None:
        return context

    schema_is_class = issubclass(context_schema, BaseModel) or is_dataclass(
        context_schema,
    )
    if isinstance(context, dict) and schema_is_class:
        try:
            return context_schema(**_filter_context_by_schema(context, graph))
        except Exception as e:
            # Context coercion errors (missing fields, type mismatches, validation errors)
            # are user errors - they're caused by invalid input to the graph
            from langgraph_grpc_common.conversion.exception import (  # noqa: PLC0415
                UserCodeExecutionErrorException,
            )

            raise UserCodeExecutionErrorException(e, node_name=None) from e

    return context


def _filter_context_by_schema(context: dict[str, Any], graph: Pregel) -> dict[str, Any]:
    json_schema = graph.get_context_jsonschema()
    if not json_schema or not context:
        return context

    # Extract valid properties from the schema
    properties = json_schema.get("properties", {})
    if not properties:
        return context

    # Filter context to only include parameters defined in the schema
    filtered_context = {}
    for key, value in context.items():
        if key in properties:
            filtered_context[key] = value

    return filtered_context
