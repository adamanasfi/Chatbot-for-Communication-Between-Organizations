from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone

import asyncpg
import openpyxl
from langchain_core.tools import tool

PATIENT_UPLOAD_COLUMNS = [
    "chief_complaint",
    "heart_rate",
    "able_to_walk",
    "spontaneous_breathing",
    "respiratory_rate",
    "radial_pulse_present",
    "obeys_commands",
]

PENDING_ATTACHMENTS: dict[str, dict] = {}
LATEST_ACUITY_SNAPSHOT: dict | None = None
TRIAGE_ORDER = ["Immediate", "Delayed", "Minor", "Expectant", "Pending"]


def classify_patient(
    *,
    able_to_walk: bool,
    spontaneous_breathing: bool,
    respiratory_rate: int | None,
    radial_pulse_present: bool,
    obeys_commands: bool,
) -> str:
    """
    START (Simple Triage And Rapid Treatment) algorithm.

    Gates, in order: ambulatory -> respirations -> perfusion -> mental status.
    """
    if able_to_walk:
        return "Minor"

    if not spontaneous_breathing:
        # Not breathing even after an airway is opened/repositioned -> unsalvageable
        # given available resources in an MCI.
        return "Expectant"

    if respiratory_rate is not None and respiratory_rate > 30:
        return "Immediate"

    if not radial_pulse_present:
        return "Immediate"

    if not obeys_commands:
        return "Immediate"

    return "Delayed"


def _record_dict(row) -> dict:
    result = {}
    for key, value in dict(row).items():
        result[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


def patient_flags(patient: dict) -> list[dict]:
    flags = []
    if patient.get("spontaneous_breathing") is False:
        flags.append({"label": "Airway", "severity": "bad"})
    if (patient.get("respiratory_rate") or 0) > 30:
        flags.append({"label": "High RR", "severity": "bad"})
    if patient.get("radial_pulse_present") is False:
        flags.append({"label": "Perfusion", "severity": "bad"})
    if patient.get("obeys_commands") is False:
        flags.append({"label": "Neuro", "severity": "bad"})
    if patient.get("able_to_walk") is False:
        flags.append({"label": "Non-ambulatory", "severity": "warn"})
    if (patient.get("heart_rate") or 0) >= 120:
        flags.append({"label": "High HR", "severity": "warn"})
    if not flags:
        flags.append({"label": "No critical START flags", "severity": "stable"})
    return flags


def patient_signal(label: str, value) -> dict:
    if value is True:
        text = "Yes"
        state = "yes"
    elif value is False:
        text = "No"
        state = "no"
    else:
        text = "-"
        state = "unknown"
    return {"label": label, "value": text, "state": state}


def patient_card(patient: dict) -> dict:
    category = patient.get("triage_category") or "Pending"
    return {
        "id": patient.get("id"),
        "category": category,
        "chief_complaint": patient.get("chief_complaint") or "No chief complaint recorded",
        "vitals": {
            "heart_rate": patient.get("heart_rate"),
            "respiratory_rate": patient.get("respiratory_rate"),
        },
        "signals": [
            patient_signal("Walks", patient.get("able_to_walk")),
            patient_signal("Breathing", patient.get("spontaneous_breathing")),
            patient_signal("Pulse", patient.get("radial_pulse_present")),
            patient_signal("Commands", patient.get("obeys_commands")),
        ],
        "flags": patient_flags(patient),
    }


def is_personnel_resource(resource_type: str | None) -> bool:
    normalized = (resource_type or "").lower()
    return any(
        keyword in normalized
        for keyword in [
            "nurse", "physician", "doctor", "surgeon", "clinician", "provider",
            "staff", "technician", "therapist", "paramedic", "emt", "resident",
            "attending", "specialist", "coordinator", "administrator",
        ]
    )


def resource_snapshot(resources: list[dict]) -> dict:
    groups = {"equipment": {}, "personnel": {}}
    for resource in resources:
        bucket = "personnel" if is_personnel_resource(resource.get("resource_type")) else "equipment"
        resource_type = resource.get("resource_type") or "Unspecified"
        groups[bucket][resource_type] = groups[bucket].get(resource_type, 0) + (resource.get("quantity") or 0)
    return {
        key: [
            {"resource_type": name, "quantity": quantity}
            for name, quantity in sorted(items.items(), key=lambda item: item[1], reverse=True)
        ]
        for key, items in groups.items()
    }


def build_acuity_snapshot(patient_rows: list[dict], resource_rows: list[dict]) -> dict:
    counts = {category: 0 for category in TRIAGE_ORDER}
    groups = {category: [] for category in TRIAGE_ORDER}
    for patient in patient_rows:
        category = patient.get("triage_category") or "Pending"
        if category not in counts:
            counts[category] = 0
            groups[category] = []
        counts[category] += 1
        groups[category].append(patient_card(patient))

    return {
        "generated_at": None,
        "source": "ER Agent",
        "counts": counts,
        "groups": groups,
        "resources": resource_snapshot(resource_rows),
    }


def summarize_patient_cards_for_agent(patients: list[dict]) -> str:
    if not patients:
        return "none"

    summaries = []
    for patient in patients:
        flags = [
            flag.get("label")
            for flag in patient.get("flags", [])
            if flag.get("label") and flag.get("label") != "No critical START flags"
        ]
        flag_text = ", ".join(flags) if flags else "no critical START flags"
        vitals = patient.get("vitals", {})
        summaries.append(
            f"#{patient.get('id')} {patient.get('chief_complaint')} "
            f"(HR {vitals.get('heart_rate')}, RR {vitals.get('respiratory_rate')}): "
            f"{flag_text}"
        )
    return "; ".join(summaries)


def summarize_full_acuity_board_for_agent(snapshot: dict) -> str:
    sections = []
    for category in TRIAGE_ORDER:
        patients = snapshot.get("groups", {}).get(category, [])
        if not patients:
            continue
        sections.append(f"{category}: {summarize_patient_cards_for_agent(patients)}")
    return " | ".join(sections) if sections else "no patients"


def get_latest_acuity_snapshot() -> dict | None:
    return LATEST_ACUITY_SNAPSHOT


class ERTriageTools:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.commit_patient_upload_tool = tool(self.commit_patient_upload)
        self.run_start_triage_tool = tool(self.run_start_triage)
        self.generate_ed_acuity_snapshot_tool = tool(self.generate_ed_acuity_snapshot)

    async def commit_patient_upload(self, upload_id: str, sheet_name: str | None = None) -> str:
        """
        Commit a staged Excel workbook sheet into the ED patients database. Use
        this only after reviewing the attached workbook content and deciding it
        is patient START triage data. This does not run START triage; rows remain
        pending until run_start_triage is called separately.
        """
        staged = PENDING_ATTACHMENTS.get(upload_id)
        if staged is None:
            return f"No staged attachment found for upload_id={upload_id}."
        if staged.get("type") != "excel_workbook":
            return f"Attachment {upload_id} is not an Excel workbook."

        sheets = staged["sheets"]
        sheet = None
        if sheet_name:
            sheet = next((s for s in sheets if s["name"] == sheet_name), None)
            if sheet is None:
                return f"No sheet named {sheet_name!r} found in attachment {upload_id}."
        elif len(sheets) == 1:
            sheet = sheets[0]
        else:
            names = ", ".join(s["name"] for s in sheets)
            return f"Attachment {upload_id} has multiple sheets. Choose one sheet_name: {names}."

        try:
            records = parse_patient_rows(sheet["rows"], source=f"sheet {sheet['name']!r}")
        except ValueError as exc:
            return f"Attachment {upload_id} cannot be committed as patients: {exc}"

        try:
            inserted = await insert_patients_batch(self.dsn, records, notify=False)
        except asyncpg.exceptions.UniqueViolationError:
            return (
                f"Attachment {upload_id} was not committed because at least one "
                "patient row already exists in the ED patients database. The "
                "staged attachment is still available; clear the existing patient "
                "rows, choose a different workbook, or edit the duplicate row "
                "before committing it."
            )
        except asyncpg.exceptions.CheckViolationError as exc:
            return (
                f"Attachment {upload_id} was not committed because the patient "
                f"data failed a database validity check: {exc}"
            )
        except asyncpg.exceptions.NotNullViolationError as exc:
            return (
                f"Attachment {upload_id} was not committed because required "
                f"patient data is missing: {exc}"
            )
        except asyncpg.exceptions.PostgresError as exc:
            return (
                f"Attachment {upload_id} was not committed because the database "
                f"rejected it: {exc}"
            )
        del PENDING_ATTACHMENTS[upload_id]
        return (
            f"Committed patient upload {upload_id} ({staged['filename']} / {sheet['name']}): "
            f"inserted {inserted} patient(s) into the ED patients database."
        )

    async def run_start_triage(self) -> str:
        """
        Run the START triage algorithm on every ED patient that does not yet have
        a triage_category, and persist the result to the database. Never assign a
        triage category yourself in free-form text -- always call this tool.
        """
        conn = await asyncpg.connect(self.dsn)
        try:
            rows = await conn.fetch(
                "SELECT id, able_to_walk, spontaneous_breathing, respiratory_rate, "
                "radial_pulse_present, obeys_commands FROM patients "
                "WHERE triage_category IS NULL"
            )
            if not rows:
                return "No patients pending triage."

            counts = {"Immediate": 0, "Delayed": 0, "Minor": 0, "Expectant": 0}
            for r in rows:
                category = classify_patient(
                    able_to_walk=bool(r["able_to_walk"]),
                    spontaneous_breathing=bool(r["spontaneous_breathing"]),
                    respiratory_rate=r["respiratory_rate"],
                    radial_pulse_present=bool(r["radial_pulse_present"]),
                    obeys_commands=bool(r["obeys_commands"]),
                )
                await conn.execute(
                    "UPDATE patients SET triage_category=$1, updated_at=now() WHERE id=$2",
                    category, r["id"],
                )
                counts[category] += 1

            summary = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
            return f"START triage complete for {len(rows)} patient(s): {summary}."
        finally:
            await conn.close()

    async def generate_ed_acuity_snapshot(self) -> str:
        """
        Calculate and store the structured ED acuity snapshot used by the Triage
        dashboard. Call this after patient data or START categories change.
        """
        global LATEST_ACUITY_SNAPSHOT
        conn = await asyncpg.connect(self.dsn)
        try:
            patient_rows = [
                _record_dict(row)
                for row in await conn.fetch("SELECT * FROM patients ORDER BY id")
            ]
            resource_rows = [
                _record_dict(row)
                for row in await conn.fetch("SELECT * FROM resources ORDER BY id")
            ]
        finally:
            await conn.close()

        snapshot = build_acuity_snapshot(patient_rows, resource_rows)
        snapshot["generated_at"] = datetime.now(timezone.utc).isoformat()
        LATEST_ACUITY_SNAPSHOT = snapshot
        counts = ", ".join(
            f"{snapshot['counts'].get(category, 0)} {category}"
            for category in TRIAGE_ORDER
            if snapshot["counts"].get(category, 0)
        ) or "no patients"
        patient_details = summarize_full_acuity_board_for_agent(snapshot)
        return (
            f"Generated ED acuity snapshot: {counts}. Full patient acuity board: "
            f"{patient_details}."
        )


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        raise ValueError("missing boolean value")
    text = str(v).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"invalid boolean value {v!r}")


def _coerce_int(v) -> int:
    if v is None or str(v).strip() == "":
        raise ValueError("missing integer value")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"invalid integer value {v!r}")


def _json_safe(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def parse_excel_workbook(content: bytes) -> list[dict]:
    """Parse any Excel workbook into sheet/header/row dictionaries."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as exc:
        raise ValueError(f"Could not read file: {exc}") from exc

    sheets = []
    for ws in wb.worksheets:
        raw_rows = [
            list(row)
            for row in ws.iter_rows(values_only=True)
            if row is not None and not all(v is None for v in row)
        ]
        if not raw_rows:
            continue
        headers = [
            str(h).strip() if h is not None and str(h).strip() else f"column_{idx}"
            for idx, h in enumerate(raw_rows[0], start=1)
        ]
        rows = []
        for row_number, row in enumerate(raw_rows[1:], start=2):
            values = list(row) + [None] * (len(headers) - len(row))
            item = {
                headers[idx]: _json_safe(values[idx]) if idx < len(values) else None
                for idx in range(len(headers))
            }
            item["_row_number"] = row_number
            rows.append(item)
        sheets.append({
            "name": ws.title,
            "headers": headers,
            "row_count": len(rows),
            "rows": rows,
        })

    if not sheets:
        raise ValueError("Empty file")
    return sheets


def parse_patient_rows(rows: list[dict], *, source: str = "rows") -> list[dict]:
    """Validate and normalize rows for insertion into the patients table."""
    headers = set()
    for row in rows:
        headers.update(k for k in row.keys() if not k.startswith("_"))
    missing_columns = [col for col in PATIENT_UPLOAD_COLUMNS if col not in headers]
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

    records = []
    seen = set()
    for index, rec in enumerate(rows, start=1):
        row_number = rec.get("_row_number", index)
        try:
            chief_complaint = str(rec.get("chief_complaint") or "").strip()
            if not chief_complaint:
                raise ValueError("missing chief_complaint")
            heart_rate = _coerce_int(rec.get("heart_rate"))
            respiratory_rate = _coerce_int(rec.get("respiratory_rate"))
            if not 0 <= heart_rate <= 250:
                raise ValueError("heart_rate must be between 0 and 250")
            if not 0 <= respiratory_rate <= 80:
                raise ValueError("respiratory_rate must be between 0 and 80")
            record = {
                "chief_complaint": chief_complaint,
                "heart_rate": heart_rate,
                "able_to_walk": _coerce_bool(rec.get("able_to_walk")),
                "spontaneous_breathing": _coerce_bool(rec.get("spontaneous_breathing")),
                "respiratory_rate": respiratory_rate,
                "radial_pulse_present": _coerce_bool(rec.get("radial_pulse_present")),
                "obeys_commands": _coerce_bool(rec.get("obeys_commands")),
            }
        except ValueError as exc:
            raise ValueError(f"Row {row_number}: {exc}") from exc

        fingerprint = (
            record["chief_complaint"].lower(),
            record["heart_rate"],
            record["able_to_walk"],
            record["spontaneous_breathing"],
            record["respiratory_rate"],
            record["radial_pulse_present"],
            record["obeys_commands"],
        )
        if fingerprint in seen:
            raise ValueError(f"Row {row_number}: duplicate patient record")
        seen.add(fingerprint)
        records.append(record)

    if not records:
        raise ValueError(f"No patient rows found in {source}")
    return records


def parse_patient_workbook(content: bytes) -> list[dict]:
    """
    Parse an uploaded Excel file into patient insert records (dicts keyed by
    PATIENT_UPLOAD_COLUMNS). Raises ValueError on anything unreadable/empty.
    """
    sheets = parse_excel_workbook(content)
    if not sheets[0]["row_count"]:
        raise ValueError("No patient rows found")
    return parse_patient_rows(sheets[0]["rows"], source=f"sheet {sheets[0]['name']!r}")


def stage_workbook_attachment(filename: str, sheets: list[dict]) -> str:
    upload_id = str(uuid.uuid4())
    PENDING_ATTACHMENTS[upload_id] = {
        "type": "excel_workbook",
        "filename": filename,
        "sheets": sheets,
    }
    return upload_id


def format_workbook_attachment_context(
    *, upload_id: str, filename: str, sheets: list[dict], user_text: str = ""
) -> str:
    sheet_summaries = [
        {
            "name": sheet["name"],
            "headers": sheet["headers"],
            "row_count": sheet["row_count"],
            "rows": sheet["rows"],
        }
        for sheet in sheets
    ]
    payload = {
        "type": "excel_workbook_attachment",
        "upload_id": upload_id,
        "filename": filename,
        "sheets": sheet_summaries,
    }
    prefix = f"{user_text.strip()}\n\n" if user_text.strip() else ""
    return prefix + "ATTACHED_FILE_CONTEXT:\n" + json.dumps(payload, indent=2)


async def insert_patients_batch(dsn: str, records: list[dict], *, notify: bool = True) -> int:
    """
    Insert patient records.

    - notify=True, exactly one record: no app.batch_mode -- the per-row
      trigger fires normally with the full row as JSON, so a DB-event
      listener sees the actual patient data (id, chief_complaint, ...) and
      can act on the single new arrival immediately (e.g. proactively
      suggest a matching clinical pathway).
    - notify=True, more than one record: app.batch_mode suppresses the
      per-row notify, and one manual pg_notify('ed_patient_updates',
      'batch') fires after commit -- the agent wakes up once and reads the
      whole table itself rather than being sent N near-simultaneous events.
    - notify=False: app.batch_mode suppresses all notification regardless
      of record count -- used when the caller (commit_patient_upload) is
      already inside an agent turn that will explicitly trigger whatever
      follow-up is needed.
    """
    suppress_per_row = (not notify) or len(records) > 1
    fire_manual_batch_notify = notify and len(records) > 1

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            if suppress_per_row:
                await conn.execute("SET LOCAL app.batch_mode = 'true'")
            for rec in records:
                await conn.execute(
                    "INSERT INTO patients (chief_complaint, heart_rate, "
                    "able_to_walk, spontaneous_breathing, respiratory_rate, "
                    "radial_pulse_present, obeys_commands) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    rec["chief_complaint"],
                    rec["heart_rate"],
                    rec["able_to_walk"],
                    rec["spontaneous_breathing"],
                    rec["respiratory_rate"],
                    rec["radial_pulse_present"],
                    rec["obeys_commands"],
                )
            if fire_manual_batch_notify:
                # One manual notification after the whole batch commits -- the
                # agent wakes up once and reads the full patients table itself.
                await conn.execute("SELECT pg_notify('ed_patient_updates', 'batch')")
        return len(records)
    finally:
        await conn.close()
