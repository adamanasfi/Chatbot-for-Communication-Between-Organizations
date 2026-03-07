from collections.abc import Sequence

from langgraph.types import All

from langgraph_grpc_common.proto import engine_common_pb2


def static_interrupt_config_to_proto(
    value: All | Sequence[str] | None,
) -> engine_common_pb2.StaticInterruptConfig | None:
    """Convert static/compile-time interrupt config to StaticInterruptConfig proto message.

    Args:
        value: Interrupt config setting - can be:
            - None: No interrupts
            - "*": Interrupt at all nodes
            - Sequence[str]: Specific node names (empty means no interrupts)

    Returns:
        StaticInterruptConfig proto message or None
    """
    if value is None:
        return None
    if value == "*":
        return engine_common_pb2.StaticInterruptConfig(all=True)
    if isinstance(value, Sequence) and not isinstance(value, str):
        if len(value) == 0:
            return None
        return engine_common_pb2.StaticInterruptConfig(
            node_names=engine_common_pb2.StaticInterruptConfig.NodeNames(
                names=list(value)
            )
        )
    raise ValueError(f"Invalid interrupt config: {value!r}")
