import os
from pathlib import Path

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from common.console_ui import register_console_routes, row_to_dict
from vehicle_tracking_tools import upsert_vehicle_tracking

STATIC_DIR = Path(__file__).parent / "static"


def register_ui_routes(
    app, agent, default_employee_thread_id: str, dsn: str
) -> None:
    google_maps_api_key = os.getenv("CIVILDEFENSE_GOOGLE_MAPS_API_KEY", "")
    maps_script = (
        f'<script src="https://maps.googleapis.com/maps/api/js?key={google_maps_api_key}'
        f'&callback=initMap&loading=async" async defer></script>'
        if google_maps_api_key else ""
    )
    register_console_routes(
        app, agent, default_employee_thread_id, dsn, STATIC_DIR,
        extra_template_vars={"__GOOGLE_MAPS_SCRIPT__": maps_script},
    )

    # ------------------------------------------------------------------ tracked-vehicle read route

    async def db_vehicle_tracking(request: Request):
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch("SELECT * FROM vehicle_tracking ORDER BY source_org, vehicle_id")
        finally:
            await conn.close()
        return JSONResponse({"rows": [row_to_dict(r) for r in rows]})

    async def delete_vehicle_tracking(request: Request):
        row_id = int(request.path_params["row_id"])
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM vehicle_tracking WHERE id=$1", row_id)
        finally:
            await conn.close()
        return JSONResponse({"ok": True})

    app.add_route("/db/vehicle_tracking", db_vehicle_tracking, methods=["GET"])
    app.add_route("/db/vehicle_tracking/{row_id}", delete_vehicle_tracking, methods=["DELETE"])

    # ------------------------------------------------------------------ partner org routes

    async def partner_vehicle_tracking(request: Request):
        """
        Non-agentic ingest endpoint: a partner org's own process (e.g. Red
        Cross's streaming loop) posts a location ping here directly over
        HTTP. This never touches the LLM or the A2A protocol -- it's a
        plain write into our own DB using our own internal DSN, which the
        partner org never sees or holds credentials for. Civil Defense's
        agent reads the result later, on demand, via list_tracked_vehicles.
        """
        body = await request.json()
        try:
            await upsert_vehicle_tracking(
                dsn,
                source_org=str(body["source_org"]),
                vehicle_id=int(body["vehicle_id"]),
                label=str(body["label"]),
                lat=float(body["lat"]),
                lng=float(body["lng"]),
                status=str(body.get("status", "active")),
            )
            return JSONResponse({"ok": True})
        except (KeyError, ValueError, TypeError) as exc:
            return JSONResponse({"ok": False, "error": f"Invalid payload: {exc}"}, status_code=400)

    app.add_route("/partner/vehicle_tracking", partner_vehicle_tracking, methods=["POST"])
