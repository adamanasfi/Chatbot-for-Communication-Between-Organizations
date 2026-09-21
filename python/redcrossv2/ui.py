import os
import random
from pathlib import Path

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from common.console_ui import register_console_routes, row_to_dict

STATIC_DIR = Path(__file__).parent / "static"


def register_ui_routes(
    app, agent, default_employee_thread_id: str, dsn: str
) -> None:
    google_maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    maps_script = (
        f'<script src="https://maps.googleapis.com/maps/api/js?key={google_maps_api_key}'
        f'&callback=initMap&loading=async" async defer></script>'
        if google_maps_api_key else ""
    )
    register_console_routes(
        app, agent, default_employee_thread_id, dsn, STATIC_DIR,
        extra_template_vars={"__GOOGLE_MAPS_SCRIPT__": maps_script},
    )

    # ------------------------------------------------------------------ vehicle routes

    async def db_vehicles(request: Request):
        if request.method == "GET":
            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch("SELECT * FROM vehicles ORDER BY id")
                return JSONResponse({"rows": [row_to_dict(r) for r in rows]})
            finally:
                await conn.close()

        elif request.method == "POST":
            body = await request.json()
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(
                    "INSERT INTO vehicles (label, lat, lng, status) VALUES ($1, $2, $3, $4)",
                    body["label"],
                    float(body["lat"]),
                    float(body["lng"]),
                    body.get("status", "active"),
                )
                return JSONResponse({"ok": True})
            finally:
                await conn.close()

    async def db_vehicle_by_id(request: Request):
        vehicle_id = int(request.path_params["vehicle_id"])

        if request.method == "PUT":
            body = await request.json()
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(
                    "UPDATE vehicles SET label=$1, lat=$2, lng=$3, status=$4, "
                    "updated_at=now() WHERE id=$5",
                    body["label"],
                    float(body["lat"]),
                    float(body["lng"]),
                    body.get("status", "active"),
                    vehicle_id,
                )
                return JSONResponse({"ok": True})
            except Exception as exc:
                print(f"[DB ERROR] PUT /db/vehicles/{vehicle_id}: {exc}")
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
            finally:
                await conn.close()

        elif request.method == "DELETE":
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute("DELETE FROM vehicles WHERE id=$1", vehicle_id)
                return JSONResponse({"ok": True})
            finally:
                await conn.close()

    # ------------------------------------------------------------------ dev/test-only route simulation
    #
    # Triggered directly by the "Simulate" button on the Vehicles page --
    # deliberately NOT an agent tool, so it never goes through chat or the
    # LLM. This is a throwaway testing aid and is expected to be deleted
    # once it's no longer needed.

    async def simulate_vehicle(request: Request):
        vehicle_id = int(request.path_params["vehicle_id"])
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow("SELECT lat, lng FROM vehicles WHERE id=$1", vehicle_id)
        finally:
            await conn.close()
        if row is None:
            return JSONResponse({"ok": False, "error": f"No vehicle found with id {vehicle_id}"}, status_code=404)

        # Random nearby point (~2-6km away) so movement is visible on the map.
        destination_lat = row["lat"] + random.uniform(-0.04, 0.04)
        destination_lng = row["lng"] + random.uniform(-0.04, 0.04)

        result = await agent.vehicle_tools.start_vehicle_route_simulation(
            vehicle_id=vehicle_id,
            destination_lat=destination_lat,
            destination_lng=destination_lng,
        )
        return JSONResponse({"ok": True, "result": result})

    async def stop_simulate_vehicle(request: Request):
        vehicle_id = int(request.path_params["vehicle_id"])
        result = await agent.vehicle_tools.stop_vehicle_route_simulation(vehicle_id)
        return JSONResponse({"ok": True, "result": result})

    app.add_route("/db/vehicles",             db_vehicles,      methods=["GET", "POST"])
    app.add_route("/db/vehicles/{vehicle_id}", db_vehicle_by_id, methods=["PUT", "DELETE"])
    app.add_route("/db/vehicles/{vehicle_id}/simulate",      simulate_vehicle,      methods=["POST"])
    app.add_route("/db/vehicles/{vehicle_id}/simulate/stop", stop_simulate_vehicle, methods=["POST"])
