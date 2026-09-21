from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Optional

import asyncpg
import httpx
from langchain_core.tools import tool

Notifier = Callable[[str], Awaitable[str]]

MIN_STREAM_INTERVAL_SECONDS = 4
DEFAULT_STREAM_INTERVAL_SECONDS = 4
MIN_ROUTE_STEP_SECONDS = 2
DEFAULT_ROUTE_STEPS = 10
DEFAULT_ROUTE_STEP_SECONDS = 2


def build_maps_link(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps?q={lat},{lng}"


async def reverse_geocode(lat: float, lng: float) -> Optional[str]:
    """
    Best-effort human-readable address for (lat, lng) via the Google Maps
    Geocoding API. Returns None (never raises) if GOOGLE_MAPS_API_KEY isn't
    configured or the request fails -- the maps link alone is always enough
    to share a location, the address is just a nicer label when available.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"latlng": f"{lat},{lng}", "key": api_key},
            )
            data = resp.json()
        results = data.get("results") or []
        return results[0]["formatted_address"] if results else None
    except Exception:
        return None


async def load_vehicle(dsn: str, vehicle_id: int) -> Optional[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, label, lat, lng, status, updated_at FROM vehicles WHERE id=$1",
            vehicle_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def move_vehicle(dsn: str, vehicle_id: int, lat: float, lng: float) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE vehicles SET lat=$1, lng=$2, updated_at=now() WHERE id=$3",
            lat, lng, vehicle_id,
        )
    finally:
        await conn.close()


async def push_vehicle_tracking(civil_defense_base_url: str, vehicle: dict[str, Any]) -> None:
    """
    Report this vehicle's current position to Civil Defense over plain
    HTTP, hitting their own non-agentic ingest endpoint (/partner/vehicle_
    tracking) -- no A2A call, no LLM inference on their side, and no
    database credentials cross organizational lines: Civil Defense writes
    into its own DB using its own DSN, exactly like it would for any other
    inbound request to its own server. This is a plain fact (where is the
    vehicle), not something needing Civil Defense's agent to reason about,
    so it must never route through send_to_civil_defense_a2a: that would
    trigger a real, billed LLM call on their end for every streaming tick,
    indefinitely, with no human involved to gate it. Civil Defense's agent
    reads the result later, on demand, via its own list_tracked_vehicles
    tool -- retrieval only costs something when a human actually asks.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(
            f"{civil_defense_base_url}/partner/vehicle_tracking",
            json={
                "source_org": "redcross",
                "vehicle_id": vehicle["id"],
                "label": vehicle["label"],
                "lat": vehicle["lat"],
                "lng": vehicle["lng"],
                "status": vehicle["status"],
            },
        )


class VehicleTools:
    """
    Vehicle location sharing for Red Cross.

    Modes, deliberately cheap on agent/LLM involvement (on both sides) and
    on Google API usage:
    - share_vehicle_location: one DB read + one optional geocode lookup +
      one A2A send, for a single point-in-time check a human asked for --
      this is the one case where costing Civil Defense's agent an LLM call
      is appropriate, since it's infrequent and human-gated.
    - start_vehicle_location_stream: the agent calls this ONCE to kick off
      a background asyncio loop that keeps pushing fresh position updates
      on a timer. Each tick writes the position directly into Civil
      Defense's own vehicle_tracking table (see push_vehicle_tracking),
      never calling send_to_civil_defense_a2a or costing an LLM call, and
      costs nothing on their side until their agent is actually asked to
      look it up. The one exception is the start call itself, which sends
      a single one-time A2A notification announcing the stream began --
      that's a one-off event, not a per-tick cost. Streamed position
      updates also skip geocoding (raw lat/lng only), since a billed
      Geocoding API request on every tick of an indefinitely-running
      stream would be real recurring cost with no human gating it.
      stop_vehicle_location_stream ends it.
    - start_vehicle_route_simulation: a dev/test-only helper, NOT exposed
      to the agent -- triggered directly by a UI button on the Vehicles
      page (see ui.py), not by chat. Fakes movement for a demo by moving a
      vehicle in a straight line toward a destination over time, purely by
      writing new positions into Red Cross's own vehicles table -- no
      external routing API, so no cost. It does not touch Civil Defense on
      its own; streaming a vehicle's position is a separate, explicit
      chat action (start_vehicle_location_stream) so the two stay
      independent. stop_vehicle_route_simulation ends it.
    """

    def __init__(
        self, dsn: str, civil_defense_base_url: Optional[str] = None,
        notifier: Optional[Notifier] = None,
    ):
        self.dsn = dsn
        self.civil_defense_base_url = civil_defense_base_url
        self.notifier = notifier
        self._streams: dict[int, asyncio.Task] = {}
        self._routes: dict[int, asyncio.Task] = {}
        self.list_vehicles_tool = tool(self.list_vehicles)
        self.share_vehicle_location_tool = tool(self.share_vehicle_location)
        self.start_vehicle_location_stream_tool = tool(self.start_vehicle_location_stream)
        self.stop_vehicle_location_stream_tool = tool(self.stop_vehicle_location_stream)
        # start/stop_vehicle_route_simulation are deliberately NOT wrapped as
        # agent tools -- they're a UI-button-only dev/test helper (see
        # ui.py's /db/vehicles/{id}/simulate routes), not something the
        # employee triggers by asking in chat.

    async def list_vehicles(self) -> str:
        """
        List all Red Cross vehicles (id, label, status) so you can resolve
        which numeric vehicle_id the employee means when they refer to a
        vehicle by name (e.g. "Ambulance 3"). share_vehicle_location and
        start_vehicle_location_stream require that numeric id -- call this
        first whenever the employee names a vehicle instead of giving its
        id directly, rather than guessing an id yourself.
        """
        conn = await asyncpg.connect(self.dsn)
        try:
            rows = await conn.fetch("SELECT id, label, status FROM vehicles ORDER BY id")
        finally:
            await conn.close()
        if not rows:
            return "No vehicles in the database yet."
        return "\n".join(f"{r['id']}: {r['label']} (status: {r['status']})" for r in rows)

    async def share_vehicle_location(self, vehicle_id: int) -> str:
        """
        Send a one-time location snapshot for one Red Cross vehicle to
        Civil Defense (the only currently-configured partner organization).
        Call this once per request for a location -- it is a single
        snapshot, not a continuous location stream. If the requester wants
        an updated position later, they must ask again and this tool is
        called again; do not call it repeatedly on your own to simulate
        live tracking, since each call is a real message send. For
        continuous/live tracking, use start_vehicle_location_stream instead.
        """
        vehicle = await load_vehicle(self.dsn, vehicle_id)
        if vehicle is None:
            return f"No vehicle found with id {vehicle_id}."

        if self.civil_defense_base_url is not None:
            try:
                await push_vehicle_tracking(self.civil_defense_base_url, vehicle)
            except Exception as exc:
                print(f"[vehicle share] tracking push failed for vehicle {vehicle_id}: {exc}")

        maps_link = build_maps_link(vehicle["lat"], vehicle["lng"])
        address = await reverse_geocode(vehicle["lat"], vehicle["lng"])
        location_desc = address or f"{vehicle['lat']}, {vehicle['lng']}"
        message = (
            f"Vehicle location snapshot -- {vehicle['label']} (status: {vehicle['status']}): "
            f"{location_desc}. Map: {maps_link}"
        )
        if self.notifier is None:
            return message
        return await self.notifier(message)

    async def _stream_loop(self, vehicle_id: int, interval_seconds: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                vehicle = await load_vehicle(self.dsn, vehicle_id)
                if vehicle is None:
                    continue
                if self.civil_defense_base_url is not None:
                    try:
                        await push_vehicle_tracking(self.civil_defense_base_url, vehicle)
                    except Exception as exc:
                        print(f"[vehicle stream] tracking push failed for vehicle {vehicle_id}: {exc}")
        except asyncio.CancelledError:
            pass

    async def start_vehicle_location_stream(
        self, vehicle_id: int, interval_seconds: int = DEFAULT_STREAM_INTERVAL_SECONDS
    ) -> str:
        """
        Start continuously sharing one vehicle's location with Civil
        Defense every `interval_seconds`, until stopped. Call this ONCE to
        start it -- after this call, updates are written directly into
        Civil Defense's own tracking table in the background on a timer,
        without invoking you or Civil Defense's agent again per update, so
        a long-running stream costs neither side any LLM tokens. It does
        NOT repeatedly post into any chat; Civil Defense's agent looks the
        live position up on its own via list_tracked_vehicles when
        actually asked. A single one-time A2A message announcing the
        stream has started is sent when you call this (that's the one
        exception, since it happens once per start, not once per tick).
        Streamed position updates themselves share raw coordinates (no
        street address) to avoid firing a billed geocoding request on
        every tick. Only call this when the employee explicitly asks for
        continuous/live tracking of a vehicle; for a single location check
        with a resolved address sent directly to Civil Defense, use
        share_vehicle_location instead. Call stop_vehicle_location_stream
        to end it.
        """
        vehicle = await load_vehicle(self.dsn, vehicle_id)
        if vehicle is None:
            return f"No vehicle found with id {vehicle_id}."

        existing = self._streams.get(vehicle_id)
        if existing is not None and not existing.done():
            return f"Vehicle {vehicle_id} ({vehicle['label']}) is already streaming."

        interval_seconds = max(MIN_STREAM_INTERVAL_SECONDS, int(interval_seconds))
        self._streams[vehicle_id] = asyncio.create_task(
            self._stream_loop(vehicle_id, interval_seconds)
        )

        if self.notifier is not None:
            try:
                await self.notifier(
                    f"Started sharing live location updates for {vehicle['label']} "
                    f"(vehicle id {vehicle_id}) every {interval_seconds}s. "
                    "Check list_tracked_vehicles for the current position."
                )
            except Exception as exc:
                print(f"[vehicle stream] start notification failed for vehicle {vehicle_id}: {exc}")

        return (
            f"Started streaming {vehicle['label']}'s location to Civil Defense "
            f"every {interval_seconds}s."
        )

    async def stop_vehicle_location_stream(self, vehicle_id: int) -> str:
        """
        Stop a previously started continuous location stream for one
        vehicle. Safe to call even if no stream is currently running for
        that vehicle.
        """
        task = self._streams.pop(vehicle_id, None)
        if task is None or task.done():
            return f"No active location stream for vehicle {vehicle_id}."
        task.cancel()
        return f"Stopped location streaming for vehicle {vehicle_id}."

    async def _route_loop(
        self, vehicle_id: int, start_lat: float, start_lng: float,
        destination_lat: float, destination_lng: float,
        steps: int, step_seconds: int,
    ) -> None:
        try:
            for step in range(1, steps + 1):
                await asyncio.sleep(step_seconds)
                fraction = step / steps
                lat = start_lat + (destination_lat - start_lat) * fraction
                lng = start_lng + (destination_lng - start_lng) * fraction
                await move_vehicle(self.dsn, vehicle_id, lat, lng)
        except asyncio.CancelledError:
            pass

    async def start_vehicle_route_simulation(
        self, vehicle_id: int, destination_lat: float, destination_lng: float,
        steps: int = DEFAULT_ROUTE_STEPS, step_seconds: int = DEFAULT_ROUTE_STEP_SECONDS,
    ) -> str:
        """
        Dev/test-only: fake a vehicle's movement by moving it in a
        straight line from its current position to (destination_lat,
        destination_lng), in `steps` even increments, `step_seconds`
        apart -- each step writes a new position into Red Cross's own
        vehicles table (visible immediately on the Vehicles map). Purely a
        local data fake: no external routing service, no cost. This is
        triggered only by the "Simulate" button on the Vehicles page, not
        by the agent -- it does not touch Civil Defense on its own;
        streaming a position is the separate, explicit
        start_vehicle_location_stream chat action. Call
        stop_vehicle_route_simulation to end it early.
        """
        vehicle = await load_vehicle(self.dsn, vehicle_id)
        if vehicle is None:
            return f"No vehicle found with id {vehicle_id}."

        existing = self._routes.get(vehicle_id)
        if existing is not None and not existing.done():
            return f"Vehicle {vehicle_id} ({vehicle['label']}) is already following a simulated route."

        steps = max(1, int(steps))
        step_seconds = max(MIN_ROUTE_STEP_SECONDS, int(step_seconds))
        self._routes[vehicle_id] = asyncio.create_task(
            self._route_loop(
                vehicle_id, vehicle["lat"], vehicle["lng"],
                destination_lat, destination_lng, steps, step_seconds,
            )
        )
        return (
            f"Started simulating {vehicle['label']}'s movement toward "
            f"({destination_lat}, {destination_lng}) over {steps} steps, "
            f"{step_seconds}s apart."
        )

    async def stop_vehicle_route_simulation(self, vehicle_id: int) -> str:
        """
        Stop a previously started route simulation for one vehicle. Safe
        to call even if none is currently running.
        """
        task = self._routes.pop(vehicle_id, None)
        if task is None or task.done():
            return f"No active route simulation for vehicle {vehicle_id}."
        task.cancel()
        return f"Stopped route simulation for vehicle {vehicle_id}."
