"""Internal routes for inmem runtime (testing/debugging only)."""

import asyncio

from langgraph_runtime_inmem.database import connect


def get_internal_routes():
    from langgraph_api.config import MIGRATIONS_PATH  # noqa: PLC0415

    try:
        from langgraph_api.middleware import http_logger  # noqa: PLC0415

        http_logger.PATHS_IGNORE.add("/internal/truncate")
    except ImportError:
        pass

    if "__inmem" not in MIGRATIONS_PATH:
        # not in a testing mode.
        return []
    from langgraph_api.route import ApiRequest, ApiResponse, ApiRoute  # noqa: PLC0415

    async def truncate(request: ApiRequest):
        """Truncate all inmem data (for testing)."""
        from langgraph_api import config as api_config  # noqa: PLC0415

        if api_config.USE_CUSTOM_CHECKPOINTER:
            from langgraph_api._checkpointer._adapter import (  # noqa: PLC0415
                CHECKPOINTER_STACK,
            )

            inner = getattr(CHECKPOINTER_STACK, "inner", None)
            if inner is not None and hasattr(inner, "clear"):
                await asyncio.to_thread(inner.clear)
        else:
            from langgraph_runtime.checkpoint import Checkpointer  # noqa: PLC0415

            await asyncio.to_thread(Checkpointer().clear)
        async with connect() as conn:
            await asyncio.to_thread(conn.clear)
        return ApiResponse({"ok": True})

    async def debug_get_raw_thread(request: ApiRequest):
        """Return raw thread from store without decryption (for testing)."""
        thread_id = request.path_params["thread_id"]
        async with connect() as conn:
            for thread in conn.store["threads"]:
                if str(thread["thread_id"]) == thread_id:
                    return ApiResponse(thread)
        return ApiResponse({"error": "not found"}, status_code=404)

    return [
        ApiRoute("/internal/truncate", truncate, methods=["POST"]),
        ApiRoute(
            "/internal/debug/thread/{thread_id}", debug_get_raw_thread, methods=["GET"]
        ),
    ]
