import traceback

from langgraph.errors import GraphInterrupt, GraphRecursionError, ParentCommand
from langgraph.types import Interrupt

from langgraph_grpc_common import serde
from langgraph_grpc_common.conversion.value import (
    any_to_serialized_value,
    command_to_proto,
)
from langgraph_grpc_common.proto import engine_common_pb2, errors_pb2


class ExecutorDependencyError(Exception):
    """Exception wrapper for executor dependency failures (e.g., RemoteCheckpointer gRPC calls).

    Distinguishes dependency/infrastructure failures from user code execution errors
    and from other executor-internal errors. Tagged as "executor_dependency_internal" in gRPC
    trailing metadata.
    """

    def __init__(self, original_exception: Exception, context: str | None = None):
        self.original_exception = original_exception
        self.context = context
        super().__init__(str(original_exception))


class UserCodeExecutionErrorException(Exception):
    """Exception wrapper for user code execution errors that need explicit marking.

    This exception is used to explicitly mark certain errors as user code errors
    (e.g., context schema validation errors) so they are checked and returned as
    UserCodeExecutionError to the client via grpc, not treated as internal errors.

    Note that we can't use UserCodeExecutionError which is protobuf message instead of Exception.
    """

    def __init__(self, original_exception: Exception, node_name: str | None = None):
        self.original_exception = original_exception
        self.node_name = node_name
        super().__init__(str(original_exception))


def exception_to_proto(
    exc: Exception,
) -> (
    errors_pb2.UserCodeExecutionError
    | engine_common_pb2.WrappedInterrupts
    | engine_common_pb2.ParentCommand
    | errors_pb2.GraphRecursionLimitError
):
    if isinstance(exc, GraphInterrupt):
        if exc.args[0]:
            # Serialize the list of Interrupts
            encoding, ser = serde.get_serializer().dumps_typed(exc.args[0])
            serialized_interrupts = engine_common_pb2.SerializedValue(
                encoding=encoding, value=ser
            )
            interrupts = interrupts_to_proto(exc.args[0])
            return engine_common_pb2.WrappedInterrupts(
                interrupts=interrupts, serialized_interrupts=serialized_interrupts
            )
        else:
            # Static interrupt (interrupt_before or interrupt_after)
            return engine_common_pb2.WrappedInterrupts(
                interrupts=[engine_common_pb2.Interrupt()]
            )
    elif isinstance(exc, ParentCommand):
        cmd = exc.args[0]
        return engine_common_pb2.ParentCommand(command=command_to_proto(cmd))
    elif isinstance(exc, GraphRecursionError):
        return errors_pb2.GraphRecursionLimitError()
    elif isinstance(exc, ExecutorDependencyError):
        # Internal executor errors (e.g., gRPC failures from RemoteCheckpointer)
        # should be re-raised to be handled as internal errors, not user code errors
        raise
    else:
        # All other exceptions (including InternalUserCodeExecutionError) are user code errors
        return errors_pb2.UserCodeExecutionError(
            error_type=exc.__class__.__name__,
            error_message=str(exc),
            traceback=traceback.format_exc(),
        )


def interrupts_to_proto(
    interrupts: tuple[Interrupt],
) -> list[engine_common_pb2.Interrupt]:
    return [
        engine_common_pb2.Interrupt(
            value=any_to_serialized_value(interrupt.value),
            id=interrupt.id,
        )
        for interrupt in interrupts
    ]
