from collections.abc import Sequence
from typing import cast

from langgraph.types import StreamMode

from langgraph_grpc_common.proto import enum_stream_mode_pb2


def stream_modes_to_proto(
    stream_mode: StreamMode | Sequence[StreamMode] | None,
) -> tuple[list[enum_stream_mode_pb2.StreamMode.ValueType], bool]:
    """Convert stream_mode to proto format and track if it was a single string.

    Returns:
        A tuple of (list of stream modes, is_single flag)
        - is_single=True if stream_mode was passed as a single string
        - is_single=False if stream_mode was passed as a list/tuple (or None, defaulting to list behavior)
    """
    stream_modes: list[enum_stream_mode_pb2.StreamMode.ValueType] = []
    is_single = True
    if stream_mode is not None:
        if isinstance(stream_mode, str):
            stream_modes.append(stream_mode_to_proto(cast("StreamMode", stream_mode)))
            is_single = True
        elif isinstance(stream_mode, (list, tuple)):
            for mode in stream_mode:
                stream_modes.append(stream_mode_to_proto(mode))
            is_single = False
    return stream_modes, is_single


_TO_PROTO_MAP = dict(
    filter(
        lambda x: x[1] != enum_stream_mode_pb2.StreamMode.unknown,
        enum_stream_mode_pb2.StreamMode.items(),
    )
)
_TO_STRING_MAP = {v: k for k, v in _TO_PROTO_MAP.items()}


def stream_mode_to_proto(
    stream_mode: StreamMode,
) -> enum_stream_mode_pb2.StreamMode.ValueType:
    return _TO_PROTO_MAP[stream_mode]


def stream_mode_from_proto(
    stream_mode: enum_stream_mode_pb2.StreamMode.ValueType,
) -> StreamMode:
    if stream_mode == enum_stream_mode_pb2.StreamMode.unknown:
        raise ValueError("Unknown stream mode")
    mode = _TO_STRING_MAP.get(stream_mode)
    if mode is None:
        raise ValueError(f"Unknown stream mode: {stream_mode}")
    return mode
