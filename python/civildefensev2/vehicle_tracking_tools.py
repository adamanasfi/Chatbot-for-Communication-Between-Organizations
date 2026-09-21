from __future__ import annotations

import asyncpg
from langchain_core.tools import tool


async def upsert_vehicle_tracking(
    dsn: str, *, source_org: str, vehicle_id: int, label: str,
    lat: float, lng: float, status: str,
) -> None:
    """
    Plain deterministic write -- called directly by the partner org's own
    process (e.g. Red Cross's streaming loop) using this DB's DSN. No A2A
    call, no LLM inference on either side: a location ping is a fact, not
    something needing an agent's judgment to relay.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO vehicle_tracking (source_org, vehicle_id, label, lat, lng, status, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, now()) "
            "ON CONFLICT (source_org, vehicle_id) DO UPDATE SET "
            "label=$3, lat=$4, lng=$5, status=$6, updated_at=now()",
            source_org, vehicle_id, label, lat, lng, status,
        )
    finally:
        await conn.close()


class VehicleTrackingTools:
    """
    Civil Defense-side read access to partner-org vehicle tracking. This is
    on-demand only: nothing writes into this class's DSN except the
    partner's own process calling upsert_vehicle_tracking directly, and
    nothing here notifies the agent proactively -- it only answers when
    asked, so tracking updates never cost an LLM call on their own.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.list_tracked_vehicles_tool = tool(self.list_tracked_vehicles)

    async def list_tracked_vehicles(self, source_org: str | None = None) -> str:
        """
        Look up the latest known position of vehicles tracked by partner
        organizations (e.g. Red Cross ambulances), as of whenever they last
        pushed an update. Call this only when the employee actually asks
        where a partner org's vehicle is -- this is a live lookup, not
        something to poll on your own. Optionally filter by source_org
        (e.g. "redcross"); omit it to see all tracked vehicles.
        """
        conn = await asyncpg.connect(self.dsn)
        try:
            if source_org:
                rows = await conn.fetch(
                    "SELECT * FROM vehicle_tracking WHERE source_org=$1 ORDER BY vehicle_id",
                    source_org,
                )
            else:
                rows = await conn.fetch("SELECT * FROM vehicle_tracking ORDER BY source_org, vehicle_id")
        finally:
            await conn.close()

        if not rows:
            return "No vehicle tracking data available yet."
        return "\n".join(
            f"[{r['source_org']}] {r['label']} (id {r['vehicle_id']}, status: {r['status']}): "
            f"{r['lat']}, {r['lng']} -- last updated {r['updated_at']:%Y-%m-%d %H:%M:%S}"
            for r in rows
        )
