from pathlib import Path

import asyncpg
from langchain_core.messages import AIMessage
from starlette.requests import Request
from starlette.responses import JSONResponse

from common.console_ui import row_to_dict, register_console_routes
from pathway_tools import advance_pathway_state, format_pathway_result, serialize_pathway_graph
from pathways import PATHWAYS
from triage import (
    format_workbook_attachment_context,
    get_latest_acuity_snapshot,
    insert_patients_batch,
    parse_excel_workbook,
    parse_patient_rows,
    parse_patient_workbook,
    stage_workbook_attachment,
)

STATIC_DIR = Path(__file__).parent / "static"


async def post_pathway_reminder_to_chat(agent, thread_id: str, text: str) -> None:
    """
    Append a pathway reminder directly into the ED Director <-> ER Agent
    thread's checkpointed state, so it shows up in the Chat tab too -- same
    technique ERA2ATools uses to record the intercoord exchange, and for the
    same reason: this is a deterministic lookup result, not something that
    needs (or should risk) an LLM call to phrase.
    """
    graph = agent.employee_graph
    config = {"configurable": {"thread_id": thread_id}}
    values = {"messages": [AIMessage(content=text)]}
    if hasattr(graph, "aupdate_state"):
        await graph.aupdate_state(config, values, as_node="agent")
    elif hasattr(graph, "update_state"):
        graph.update_state(config, values, as_node="agent")


def register_ui_routes(
    app, agent, default_employee_thread_id: str, dsn: str
) -> None:
    register_console_routes(app, agent, default_employee_thread_id, dsn, STATIC_DIR)

    # ------------------------------------------------------------------ patients / triage routes

    async def db_patients(request: Request):
        if request.method == "GET":
            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch("SELECT * FROM patients ORDER BY id")
                return JSONResponse({"rows": [row_to_dict(r) for r in rows]})
            finally:
                await conn.close()

        elif request.method == "POST":
            body = await request.json()
            try:
                [record] = parse_patient_rows([body], source="manual entry")
            except ValueError as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            try:
                inserted = await insert_patients_batch(dsn, [record])
            except Exception as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            return JSONResponse({"ok": True, "inserted": inserted})

    async def db_patients_upload(request: Request):
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            return JSONResponse({"ok": False, "error": "Missing 'file'"}, status_code=400)

        content = await upload.read()
        try:
            records = parse_patient_workbook(content)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        try:
            inserted = await insert_patients_batch(dsn, records)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "inserted": inserted})

    async def employee_patient_attachment(request: Request):
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            return JSONResponse({"ok": False, "error": "Missing 'file'"}, status_code=400)

        content = await upload.read()
        try:
            sheets = parse_excel_workbook(content)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        filename = getattr(upload, "filename", None) or "patient_workbook.xlsx"
        upload_id = stage_workbook_attachment(filename, sheets)
        user_text = str(form.get("text", "")).strip()
        thread_id = str(form.get("thread_id", default_employee_thread_id))
        message = format_workbook_attachment_context(
            upload_id=upload_id,
            filename=filename,
            sheets=sheets,
            user_text=user_text,
        )
        try:
            reply = await agent.run(message, thread_id=thread_id)
        except Exception as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        "The ER agent could not finish reviewing this attachment: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
                status_code=500,
            )
        row_count = sum(sheet["row_count"] for sheet in sheets)
        return JSONResponse({
            "ok": True,
            "upload_id": upload_id,
            "record_count": row_count,
            "reply": reply,
        })

    async def db_patient_by_id(request: Request):
        patient_id = int(request.path_params["patient_id"])

        if request.method == "PUT":
            body = await request.json()

            def to_bool(value):
                if value in (True, False, None):
                    return value
                text = str(value).strip().lower()
                if text in {"yes", "true", "1"}:
                    return True
                if text in {"no", "false", "0"}:
                    return False
                return None

            def to_int(value):
                if value in ("", None):
                    return None
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(
                    "UPDATE patients SET chief_complaint=$1, heart_rate=$2, able_to_walk=$3, "
                    "spontaneous_breathing=$4, respiratory_rate=$5, radial_pulse_present=$6, "
                    "obeys_commands=$7, triage_category=$8, updated_at=now() WHERE id=$9",
                    str(body.get("chief_complaint", "")).strip() or None,
                    to_int(body.get("heart_rate")),
                    to_bool(body.get("able_to_walk")),
                    to_bool(body.get("spontaneous_breathing")),
                    to_int(body.get("respiratory_rate")),
                    to_bool(body.get("radial_pulse_present")),
                    to_bool(body.get("obeys_commands")),
                    str(body.get("triage_category", "")).strip() or None,
                    patient_id,
                )
                return JSONResponse({"ok": True})
            except Exception as exc:
                print(f"[DB ERROR] PUT /db/patients/{patient_id}: {exc}")
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            finally:
                await conn.close()

        if request.method == "DELETE":
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute("DELETE FROM patients WHERE id=$1", patient_id)
                return JSONResponse({"ok": True})
            finally:
                await conn.close()

    async def triage_summary(request: Request):
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch("SELECT * FROM patients ORDER BY id")
        finally:
            await conn.close()
        counts = {"Immediate": 0, "Delayed": 0, "Minor": 0, "Expectant": 0, "Pending": 0}
        for r in rows:
            cat = r["triage_category"] or "Pending"
            counts[cat] = counts.get(cat, 0) + 1
        return JSONResponse({
            "counts": counts,
            "rows": [row_to_dict(r) for r in rows],
        })

    async def triage_snapshot(request: Request):
        snapshot = get_latest_acuity_snapshot()
        if snapshot is None:
            return JSONResponse({
                "ok": False,
                "error": "No agent-generated acuity snapshot yet.",
                "snapshot": None,
            })
        return JSONResponse({"ok": True, "snapshot": snapshot})

    # ------------------------------------------------------------------ clinical pathway routes

    async def pathways_list(request: Request):
        return JSONResponse({
            "pathways": [
                {"id": p.id, "name": p.name, "keywords": p.chief_complaint_keywords}
                for p in PATHWAYS.values()
            ]
        })

    async def pathway_graph(request: Request):
        pathway_id = request.path_params["pathway_id"]
        pathway = PATHWAYS.get(pathway_id)
        if pathway is None:
            return JSONResponse({"ok": False, "error": f"Unknown pathway_id {pathway_id!r}"}, status_code=404)
        return JSONResponse({"ok": True, "graph": serialize_pathway_graph(pathway)})

    async def pathway_state(request: Request):
        patient_id = request.query_params.get("patient_id")
        pathway_id = request.query_params.get("pathway_id")
        if not patient_id or not pathway_id:
            return JSONResponse({"ok": False, "error": "patient_id and pathway_id are required"}, status_code=400)
        result = await advance_pathway_state(dsn, int(patient_id), pathway_id)
        if "error" in result:
            return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)
        return JSONResponse({
            "ok": True,
            "state": result,
            "reply": format_pathway_result(PATHWAYS[pathway_id], result),
        })

    async def pathway_advance(request: Request):
        body = await request.json()
        patient_id = body.get("patient_id")
        pathway_id = body.get("pathway_id")
        node_id = body.get("node_id")
        answer = body.get("answer")
        if not patient_id or not pathway_id or not node_id or not answer:
            return JSONResponse(
                {"ok": False, "error": "patient_id, pathway_id, node_id, and answer are required"},
                status_code=400,
            )
        result = await advance_pathway_state(
            dsn, int(patient_id), pathway_id, node_id, answer,
            hcc_notifier=agent.tools_service.send_to_hcc_a2a,
        )
        if "error" in result:
            return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)

        reply = format_pathway_result(PATHWAYS[pathway_id], result)
        await post_pathway_reminder_to_chat(agent, default_employee_thread_id, reply)

        return JSONResponse({"ok": True, "state": result, "reply": reply})

    app.add_route("/db/patients",         db_patients,        methods=["GET", "POST"])
    app.add_route("/db/patients/upload",  db_patients_upload, methods=["POST"])
    app.add_route("/employee/patient_attachment", employee_patient_attachment, methods=["POST"])
    app.add_route("/db/patients/{patient_id}", db_patient_by_id, methods=["PUT", "DELETE"])
    app.add_route("/triage/summary",      triage_summary,     methods=["GET"])
    app.add_route("/triage/snapshot",     triage_snapshot,    methods=["GET"])
    app.add_route("/pathways",                    pathways_list,  methods=["GET"])
    app.add_route("/pathways/{pathway_id}/graph", pathway_graph,  methods=["GET"])
    app.add_route("/pathways/state",              pathway_state,  methods=["GET"])
    app.add_route("/pathways/advance",            pathway_advance, methods=["POST"])
