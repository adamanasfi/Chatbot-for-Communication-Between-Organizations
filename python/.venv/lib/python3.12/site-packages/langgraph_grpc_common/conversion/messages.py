import logging
import uuid
from typing import Any, cast

import orjson
from google.protobuf import wrappers_pb2
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    BaseMessageChunk,
    HumanMessage,
    HumanMessageChunk,
    InvalidToolCall,
    SystemMessage,
    SystemMessageChunk,
    ToolCall,
    ToolMessage,
    ToolMessageChunk,
    convert_to_messages,
)
from langchain_core.messages.ai import (
    InputTokenDetails,
    OutputTokenDetails,
    UsageMetadata,
)

from langgraph_grpc_common import conversion, serde
from langgraph_grpc_common.proto import engine_common_pb2
from langgraph_grpc_common.sanitize import encoded_str

logger = logging.getLogger(__name__)


def _safe_text(txt: str) -> str:
    """Return UTF-8-safe string for proto string fields."""
    try:
        txt.encode("utf-8")
        return txt
    except UnicodeEncodeError:
        logger.info(
            "Removing invalid unicode from message content.",
        )
        return txt.encode("utf-8", "replace").decode("utf-8")


def _content(txt: str) -> engine_common_pb2.Content:
    return engine_common_pb2.Content(text=_safe_text(txt))


def _content_to_proto(content: str | list | None) -> engine_common_pb2.Content:
    """Convert Python message content to Content proto.

    Mirrors the Python type: str | list[str | dict]
    """
    if content is None:
        return engine_common_pb2.Content()

    if isinstance(content, str):
        return _content(content)

    if isinstance(content, list):
        blocks = []
        for item in content:
            if isinstance(item, str):
                blocks.append(engine_common_pb2.ContentBlock(text=_safe_text(item)))
            else:
                # omit index and type
                data_for_json = {
                    k: v for k, v in item.items() if k not in ("index", "type")
                }
                structured_block = engine_common_pb2.StructuredBlock(
                    data_json=orjson.dumps(data_for_json)
                )
                if isinstance(item, dict):
                    if "index" in item:
                        structured_block.index = item["index"]
                    if "type" in item:
                        structured_block.type = item["type"]
                blocks.append(
                    engine_common_pb2.ContentBlock(structured=structured_block)
                )
        return engine_common_pb2.Content(
            blocks=engine_common_pb2.ContentBlocks(items=blocks)
        )
    return engine_common_pb2.Content(text=orjson.dumps(content).decode("utf-8"))


def _content_from_proto(content: engine_common_pb2.Content) -> str | list | None:
    """Convert Content proto to Python message content.

    Returns: str | list[str | dict]
    """
    which = content.WhichOneof("content")

    if which == "text":
        return content.text

    if which == "blocks":
        result = []
        for block in content.blocks.items:
            block_which = block.WhichOneof("data")
            if block_which == "text":
                result.append(block.text)
            elif block_which == "structured":
                data = orjson.loads(block.structured.data_json)
                # Restore index and type from top-level fields if present
                if block.structured.HasField("index"):
                    data["index"] = block.structured.index
                if block.structured.HasField("type"):
                    data["type"] = block.structured.type
                result.append(data)
        return result
    return None


def chat_message_to_proto(
    msg: BaseMessage,
    meta: dict[str, Any] | None = None,
    ns: tuple[str, ...] | None = None,
) -> engine_common_pb2.ChatMessageEnvelope:
    ai_msg_fields = None
    tool_msg_fields = None
    human_msg_fields = None
    if isinstance(msg, AIMessage):
        ai_msg_fields = _get_ai_message_fields(msg)

    elif isinstance(msg, ToolMessage):
        tool_msg_fields = engine_common_pb2.ToolFields(
            tool_call_id=wrappers_pb2.StringValue(value=msg.tool_call_id),
            status=msg.status,
            artifact=(
                conversion.value.any_to_serialized_value(msg.artifact)
                if msg.artifact
                else None
            ),
        )
    elif isinstance(msg, HumanMessage):
        human_msg_fields = engine_common_pb2.HumanFields()
    model_extra = msg.model_extra
    additional_kwargs = msg.additional_kwargs
    msg_id = getattr(msg, "id", None) or uuid.uuid4().hex
    # Mutate message so ids persist through channel serialization
    msg.id = msg_id
    message = engine_common_pb2.ChatMessage(
        id=msg_id,
        name=encoded_str(getattr(msg, "name", "")),
        type=msg.type,
        content=_content_to_proto(msg.content),
        ai=ai_msg_fields,
        tool=tool_msg_fields,
        human=human_msg_fields,
    )
    # Set map fields separately since they can't be in constructor
    if additional_kwargs:
        message.additional_kwargs.update(
            conversion.struct.raw_map_from_dict(additional_kwargs)
        )
    if model_extra:
        message.extensions.update(conversion.struct.raw_map_from_dict(model_extra))
    metadata_bytes = orjson.dumps(meta) if meta else None
    return engine_common_pb2.ChatMessageEnvelope(
        is_streaming_chunk=isinstance(msg, BaseMessageChunk),
        message=message,
        namespace=ns,
        metadata=metadata_bytes,
        node_name=meta.get("langgraph_node") if meta else None,
    )


_TYPE_MAP = {
    "AIMessageChunk": AIMessageChunk,
    "ToolMessageChunk": ToolMessageChunk,
    "HumanMessageChunk": HumanMessageChunk,
    "SystemMessageChunk": SystemMessageChunk,
    "HumanMessage": HumanMessage,
    "AIMessage": AIMessage,
    "ToolMessage": ToolMessage,
    "SystemMessage": SystemMessage,
    "ai": AIMessage,
    "tool": ToolMessage,
    "human": HumanMessage,
    "user": HumanMessage,
    "system": SystemMessage,
    "developer": SystemMessage,
}


def chat_message_tuple_from_proto(
    msg: engine_common_pb2.ChatMessageEnvelope,
) -> tuple[BaseMessage, dict[str, Any]]:
    chat_messsage = chat_message_from_proto(msg)
    return chat_messsage, (
        normalize_metadata(orjson.loads(msg.metadata)) if msg.metadata else {}
    )


def chat_message_from_proto(
    msg: engine_common_pb2.ChatMessageEnvelope,
) -> BaseMessage:
    chat_message = msg.message
    m = {
        "id": chat_message.id,
        "name": chat_message.name or None,
        "type": chat_message.type,
        "content": _content_from_proto(chat_message.content),
        "additional_kwargs": (
            conversion.struct.dict_from_raw_map(chat_message.additional_kwargs)
            if chat_message.additional_kwargs
            else {}
        ),
    }
    details = chat_message.WhichOneof("details")
    if details == "ai":
        if chat_message.ai.invalid_tool_calls:
            m["invalid_tool_calls"] = [
                InvalidToolCall(
                    type="invalid_tool_call",
                    name=tc.name.value,
                    args=tc.args.value,
                    id=tc.id.value,
                    error=tc.error.value,
                )
                for tc in chat_message.ai.invalid_tool_calls
            ]
        if chat_message.ai.tool_calls:
            m["tool_calls"] = [
                ToolCall(
                    name=tc.name,
                    args=orjson.loads(tc.args_json) if tc.args_json else {},
                    id=tc.id.value,
                )
                for tc in chat_message.ai.tool_calls
            ]
        if chat_message.ai.HasField("response_metadata"):
            m["response_metadata"] = (
                conversion.struct.dict_from_raw_map(
                    chat_message.ai.response_metadata.data
                )
                if chat_message.ai.response_metadata.data
                else {}
            )
        if chat_message.ai.HasField("usage_metadata"):
            usage_metadata_struct = chat_message.ai.usage_metadata
            usage_metadata = UsageMetadata(
                input_tokens=usage_metadata_struct.input_tokens,
                output_tokens=usage_metadata_struct.output_tokens,
                total_tokens=usage_metadata_struct.total_tokens,
            )
            if usage_metadata_struct.HasField("input_token_details"):
                usage_metadata["input_token_details"] = InputTokenDetails(
                    audio=usage_metadata_struct.input_token_details.audio.value,
                    cache_creation=usage_metadata_struct.input_token_details.cache_creation.value,
                    cache_read=usage_metadata_struct.input_token_details.cache_read.value,
                )
            if usage_metadata_struct.HasField("output_token_details"):
                usage_metadata["output_token_details"] = OutputTokenDetails(
                    audio=usage_metadata_struct.output_token_details.audio.value,
                    reasoning=usage_metadata_struct.output_token_details.reasoning.value,
                )

            m["usage_metadata"] = usage_metadata
        if chat_message.ai.HasField("chunk_position"):
            m["chunk_position"] = chat_message.ai.chunk_position
        if chat_message.ai.HasField("reasoning_content"):
            m["reasoning_content"] = chat_message.ai.reasoning_content.value

    elif details == "human":
        pass
    elif details == "tool":
        m["tool_call_id"] = chat_message.tool.tool_call_id.value
        m["status"] = chat_message.tool.status
        if chat_message.tool.HasField("artifact"):
            m["artifact"] = conversion.value.serialized_value_from_proto(
                chat_message.tool.artifact
            )
    elif details is None:
        pass
    else:
        raise ValueError(f"Unknown message type: {details}")

    if chat_message.extensions:
        extensions = conversion.struct.dict_from_raw_map(chat_message.extensions)
        for key, val in extensions.items():
            m[key] = val

    msg_cls = _TYPE_MAP.get(chat_message.type)
    if not msg_cls:
        logger.error(f"Unknown message type: {chat_message.type}")
        return convert_to_messages((m,))[0]
    if chat_message.type == "developer":
        additional_kwargs = cast("dict[str, Any]", m.get("additional_kwargs") or {})
        additional_kwargs["__openai_role__"] = "developer"
        m["additional_kwargs"] = additional_kwargs
    return msg_cls(**m)  # type: ignore[no-matching-overload, invalid-argument-type]


_TUPLE_KEYS = (
    "langgraph_triggers",
    "langgraph_path",
)


def normalize_metadata(metadata: dict[str, Any] | bytes | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if isinstance(metadata, bytes):
        metadata = cast("dict[str, Any]", orjson.loads(metadata))

    for k in _TUPLE_KEYS:
        if k in metadata:
            metadata[k] = tuple(metadata[k])
    return metadata


# Msg -> proto


def _get_ai_usage_metadata(msg: AIMessage) -> engine_common_pb2.UsageMetadata | None:
    if (um := msg.usage_metadata) and um is not None:
        input_token_details = output_token_details = None
        if "input_token_details" in um and (itd := um["input_token_details"]):
            input_token_details = engine_common_pb2.InputTokenDetails(
                audio=wrappers_pb2.Int64Value(value=itd.get("audio")),
                cache_creation=wrappers_pb2.Int64Value(value=itd.get("cache_creation")),
                cache_read=wrappers_pb2.Int64Value(value=itd.get("cache_read")),
            )
        if "output_token_details" in um and (otd := um["output_token_details"]):
            output_token_details = engine_common_pb2.OutputTokenDetails(
                audio=wrappers_pb2.Int64Value(value=otd.get("audio")),
                reasoning=wrappers_pb2.Int64Value(value=otd.get("reasoning")),
            )
        usage_metadata = engine_common_pb2.UsageMetadata(
            input_token_details=input_token_details,
            output_token_details=output_token_details,
            input_tokens=um["input_tokens"],
            output_tokens=um["output_tokens"],
            total_tokens=um["total_tokens"],
        )
        return usage_metadata


def _get_ai_message_fields(msg: AIMessage) -> engine_common_pb2.AIFields | None:
    usage_metadata = _get_ai_usage_metadata(msg)
    tool_calls = _get_ai_tool_calls(msg)
    invalid_tool_calls = _get_ai_invalid_tool_calls(msg)
    tool_call_chunks = _get_ai_tool_call_chunks(msg)
    chunk_position = getattr(msg, "chunk_position", None)
    ai_msg_fields = engine_common_pb2.AIFields(
        usage_metadata=usage_metadata,
        tool_call_chunks=tool_call_chunks,
        invalid_tool_calls=invalid_tool_calls,
        tool_calls=tool_calls,
        chunk_position=chunk_position,
    )

    # Set response_metadata map field separately
    if msg.response_metadata:
        ai_msg_fields.response_metadata.data.update(
            conversion.struct.raw_map_from_dict(msg.response_metadata)
        )
    return ai_msg_fields


def _get_ai_tool_calls(msg: AIMessage) -> list[engine_common_pb2.ToolCall] | None:
    if msg.tool_calls:
        return [
            engine_common_pb2.ToolCall(
                name=encoded_str(call["name"]),
                args_json=serde.json_dumpb(call["args"]),
                id=wrappers_pb2.StringValue(value=call["id"]),
            )
            for call in msg.tool_calls
        ]


def _get_ai_invalid_tool_calls(
    msg: AIMessage,
) -> list[engine_common_pb2.InvalidToolCall] | None:
    if msg.invalid_tool_calls:
        return [
            engine_common_pb2.InvalidToolCall(
                name=wrappers_pb2.StringValue(value=call["name"]),
                args=wrappers_pb2.StringValue(value=call["args"]),
                id=wrappers_pb2.StringValue(value=call["id"]),
                error=wrappers_pb2.StringValue(value=call["error"]),
            )
            for call in msg.invalid_tool_calls
        ]


def _get_ai_tool_call_chunks(
    msg: AIMessage,
) -> list[engine_common_pb2.ToolCallChunk] | None:
    if isinstance(msg, AIMessageChunk):
        if tool_call_chunks_ := msg.tool_call_chunks:
            return [
                engine_common_pb2.ToolCallChunk(
                    name=wrappers_pb2.StringValue(value=chunk["name"]),
                    args_json=wrappers_pb2.StringValue(value=chunk["args"]),
                    id=wrappers_pb2.StringValue(value=chunk["id"]),
                    index=wrappers_pb2.Int32Value(value=chunk["index"]),
                )
                for chunk in tool_call_chunks_
            ]
