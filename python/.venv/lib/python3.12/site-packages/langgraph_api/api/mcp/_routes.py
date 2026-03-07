from __future__ import annotations

from starlette.responses import Response

from langgraph_api.api.mcp._handlers import (
    handle_delete_request,
    handle_get_request,
    handle_post_request,
)
from langgraph_api.route import ApiRequest, ApiRoute

__all__ = ["mcp_routes"]


async def handle_mcp_endpoint(request: ApiRequest) -> Response:
    """MCP endpoint handler the implements the Streamable HTTP protocol.

    The handler is expected to support the following methods:

    - POST: Process a JSON-RPC request
    - DELETE: Terminate a session

    We currently do not support:
    - /GET (initiates a streaming session)
        This endpoint can be used to RESUME a previously interrupted session.
    - text/event-stream (streaming) response from the server.

    Support for these can be added, we just need to determine what information
    from the agent we want to stream.

    One possibility is to map "custom" stream mode to server side notifications.

    Args:
        request: The incoming request object

    Returns:
        The response to the request
    """
    # Route request based on HTTP method
    if request.method == "DELETE":
        return handle_delete_request()
    elif request.method == "GET":
        return handle_get_request()
    elif request.method == "POST":
        return await handle_post_request(request)
    else:
        # Method not allowed
        return Response(status_code=405)


# Define routes for the MCP endpoint
mcp_routes = [
    ApiRoute("/mcp", handle_mcp_endpoint, methods=["GET", "POST", "DELETE"]),
]
