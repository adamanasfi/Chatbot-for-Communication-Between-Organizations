import logging
from collections.abc import Mapping
from typing import Any

from langgraph.channels.any_value import AnyValue
from langgraph.channels.base import BaseChannel, EmptyChannelError
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.channels.ephemeral_value import EphemeralValue
from langgraph.channels.last_value import LastValue, LastValueAfterFinish
from langgraph.channels.named_barrier_value import (
    NamedBarrierValue,
    NamedBarrierValueAfterFinish,
)
from langgraph.channels.topic import Topic
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.managed.base import (
    ManagedValue,
    ManagedValueMapping,
    ManagedValueSpec,
    is_managed_value,
)
from langgraph.pregel import Pregel

from langgraph_grpc_common.conversion._compat import MISSING
from langgraph_grpc_common.conversion.exception import UserCodeExecutionErrorException
from langgraph_grpc_common.conversion.value import value_from_proto, value_to_proto
from langgraph_grpc_common.proto import engine_common_pb2, executor_api_pb2

logger = logging.getLogger(__name__)


def channel_to_proto(name: str, channel: BaseChannel) -> engine_common_pb2.Channel:
    try:
        get_result = channel.get()
    except EmptyChannelError:
        get_result = MISSING

    return engine_common_pb2.Channel(
        get_result=value_to_proto(name, get_result),
        is_available_result=channel.is_available(),
        checkpoint_result=value_to_proto(name, channel.checkpoint()),
    )


def channels_to_proto(
    channels: Mapping[str, ManagedValueSpec | BaseChannel | type[ManagedValue]],
) -> engine_common_pb2.Channels:
    pb = {}
    for name, channel in channels.items():
        if isinstance(channel, BaseChannel):
            pb[name] = channel_to_proto(name, channel)
        else:
            logger.warning(
                "Unrecognized channel value: %s | %s", type(channel), channel
            )
    return engine_common_pb2.Channels(channels=pb)


def channels_specs_to_proto(
    channels: Mapping[str, ManagedValueSpec | BaseChannel | type[ManagedValue]],
) -> dict[str, executor_api_pb2.ChannelSpec]:
    """Get static channel specs (kind, params) from graph channels. ChannelSpecs is a simple message and by passing it back it lets us avoid deserializing blobs in Go runtime."""
    specs: dict[str, executor_api_pb2.ChannelSpec] = {}
    for name, ch in channels.items():
        kind: executor_api_pb2.ChannelSpec.Kind.ValueType
        params = executor_api_pb2.ChannelParams()

        if is_managed_value(ch):
            kind = executor_api_pb2.ChannelSpec.MANAGED_VALUE
        elif isinstance(ch, LastValue):
            kind = executor_api_pb2.ChannelSpec.LAST_VALUE
            params.initial_value.CopyFrom(value_to_proto(name, ch.value))
        elif isinstance(ch, AnyValue):
            kind = executor_api_pb2.ChannelSpec.ANY_VALUE
            params.initial_value.CopyFrom(value_to_proto(name, ch.value))
        elif isinstance(ch, EphemeralValue):
            kind = executor_api_pb2.ChannelSpec.EPHEMERAL_VALUE
            params.guard = ch.guard
            params.initial_value.CopyFrom(value_to_proto(name, ch.value))
        elif isinstance(ch, UntrackedValue):  # special case
            kind = executor_api_pb2.ChannelSpec.UNTRACKED_VALUE
            params.guard = ch.guard
            params.initial_value.CopyFrom(value_to_proto(name, ch.value))
        elif isinstance(ch, BinaryOperatorAggregate):
            kind = executor_api_pb2.ChannelSpec.BINARY_OPERATOR_AGGREGATE
            params.initial_value.CopyFrom(value_to_proto(name, ch.value))
        elif isinstance(ch, Topic):
            kind = executor_api_pb2.ChannelSpec.TOPIC
            params.accumulate = ch.accumulate
            params.initial_value.CopyFrom(value_to_proto(name, ch.values))
        elif isinstance(ch, NamedBarrierValue):
            kind = executor_api_pb2.ChannelSpec.NAMED_BARRIER_VALUE
            params.names.extend(list(ch.names))
            params.initial_value.CopyFrom(value_to_proto(name, list(ch.seen)))
        elif isinstance(ch, LastValueAfterFinish):
            kind = executor_api_pb2.ChannelSpec.LAST_VALUE_AFTER_FINISH
            params.initial_value.CopyFrom(value_to_proto(name, ch.value))
        elif isinstance(ch, NamedBarrierValueAfterFinish):
            kind = executor_api_pb2.ChannelSpec.NAMED_BARRIER_VALUE_AFTER_FINISH
            params.names.extend(list(ch.names))
            params.initial_value.CopyFrom(
                value_to_proto(name, [list(ch.seen), ch.finished])
            )
        else:
            kind = executor_api_pb2.ChannelSpec.UNKNOWN

        spec = executor_api_pb2.ChannelSpec(kind=kind, params=params)
        specs[name] = spec

    return specs


def channels_from_proto(
    channels_pb: dict[str, engine_common_pb2.Channel] | Any,
    graph: Pregel,
    *,
    clear_after_read: bool = False,
) -> tuple[dict[str, BaseChannel], ManagedValueMapping]:
    """Convert proto channels to Python channel objects.

    Args:
        channels_pb: Proto channel map from the request.
        graph: The Pregel graph containing channel definitions.
        clear_after_read: If True, clear each channel proto after conversion
            to free memory incrementally. This is useful when channels contain
            large data to avoid holding both serialized and deserialized forms
            in memory simultaneously.

    Returns:
        Tuple of (channels dict, managed values dict).
    """
    channels = {}
    managed = {}
    for k, channel_pb in channels_pb.items():
        if k not in graph.channels:
            raise UserCodeExecutionErrorException(
                ValueError(f"Channel '{k}' not found in graph definition"),
                node_name=None,
            )
        v = graph.channels[k]
        if isinstance(v, BaseChannel):
            channels[k] = revive_channel(v, channel_pb)
        elif is_managed_value(v):  # managed values
            managed[k] = v
        else:
            raise NotImplementedError(f"Unrecognized channel value: {type(v)} | {v}")

        if clear_after_read:
            channel_pb.Clear()

    return channels, managed


def revive_channel(
    channel: BaseChannel, channel_pb: engine_common_pb2.Channel
) -> BaseChannel:
    val_pb = channel_pb.checkpoint_result
    val = value_from_proto(val_pb)
    # Channels typically check "if checkpoint is not MISSING"; passing None can set
    # internal state to None and cause AttributeError on a later .copy(). Use MISSING
    # when there is no checkpoint for any channel type.
    safe_val = MISSING if (val is None or val is MISSING) else val
    return channel.copy().from_checkpoint(safe_val)
