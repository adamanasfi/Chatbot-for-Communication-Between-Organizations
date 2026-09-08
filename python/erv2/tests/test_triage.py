"""
Unit tests for triage.py -- pure functions, no database needed.
"""
import io
import json

import openpyxl
import pytest

from triage import (
    PENDING_ATTACHMENTS,
    build_acuity_snapshot,
    classify_patient,
    format_workbook_attachment_context,
    parse_excel_workbook,
    parse_patient_workbook,
    stage_workbook_attachment,
    summarize_full_acuity_board_for_agent,
    summarize_patient_cards_for_agent,
)

HEADERS = [
    "chief_complaint", "heart_rate", "able_to_walk", "spontaneous_breathing",
    "respiratory_rate", "radial_pulse_present", "obeys_commands",
]


def _make_workbook_bytes(rows, headers=HEADERS):
    wb = openpyxl.Workbook()
    ws = wb.active
    if headers is not None:
        ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- classify_patient

class TestClassifyPatient:
    def test_ambulatory_is_minor(self):
        assert classify_patient(
            able_to_walk=True, spontaneous_breathing=True,
            respiratory_rate=16, radial_pulse_present=True, obeys_commands=True,
        ) == "Minor"

    def test_ambulatory_overrides_bad_vitals(self):
        # able_to_walk is the first gate -- it wins even if everything else looks dire
        assert classify_patient(
            able_to_walk=True, spontaneous_breathing=True,
            respiratory_rate=40, radial_pulse_present=False, obeys_commands=False,
        ) == "Minor"

    def test_not_breathing_is_expectant(self):
        assert classify_patient(
            able_to_walk=False, spontaneous_breathing=False,
            respiratory_rate=None, radial_pulse_present=False, obeys_commands=False,
        ) == "Expectant"

    def test_high_respiratory_rate_is_immediate(self):
        assert classify_patient(
            able_to_walk=False, spontaneous_breathing=True,
            respiratory_rate=31, radial_pulse_present=True, obeys_commands=True,
        ) == "Immediate"

    def test_respiratory_rate_boundary_30_is_not_immediate(self):
        # gate is "> 30", not ">= 30"
        assert classify_patient(
            able_to_walk=False, spontaneous_breathing=True,
            respiratory_rate=30, radial_pulse_present=True, obeys_commands=True,
        ) == "Delayed"

    def test_no_radial_pulse_is_immediate(self):
        assert classify_patient(
            able_to_walk=False, spontaneous_breathing=True,
            respiratory_rate=20, radial_pulse_present=False, obeys_commands=True,
        ) == "Immediate"

    def test_does_not_obey_commands_is_immediate(self):
        assert classify_patient(
            able_to_walk=False, spontaneous_breathing=True,
            respiratory_rate=20, radial_pulse_present=True, obeys_commands=False,
        ) == "Immediate"

    def test_all_clear_is_delayed(self):
        assert classify_patient(
            able_to_walk=False, spontaneous_breathing=True,
            respiratory_rate=20, radial_pulse_present=True, obeys_commands=True,
        ) == "Delayed"

    def test_respiratory_rate_none_does_not_crash(self):
        # None RR must not raise, and must not accidentally satisfy "> 30"
        assert classify_patient(
            able_to_walk=False, spontaneous_breathing=True,
            respiratory_rate=None, radial_pulse_present=True, obeys_commands=True,
        ) == "Delayed"


# ---------------------------------------------------------------- build_acuity_snapshot

class TestBuildAcuitySnapshot:
    def test_groups_patients_and_resources_for_dashboard(self):
        snapshot = build_acuity_snapshot(
            patient_rows=[
                {
                    "id": 1,
                    "chief_complaint": "Crush injury, leg",
                    "heart_rate": 110,
                    "able_to_walk": False,
                    "spontaneous_breathing": True,
                    "respiratory_rate": 34,
                    "radial_pulse_present": True,
                    "obeys_commands": True,
                    "triage_category": "Immediate",
                },
                {
                    "id": 2,
                    "chief_complaint": "Laceration, forearm",
                    "heart_rate": 88,
                    "able_to_walk": True,
                    "spontaneous_breathing": True,
                    "respiratory_rate": 16,
                    "radial_pulse_present": True,
                    "obeys_commands": True,
                    "triage_category": "Minor",
                },
            ],
            resource_rows=[
                {"resource_type": "ICU bed", "quantity": 2},
                {"resource_type": "Nurse", "quantity": 4},
            ],
        )
        assert snapshot["counts"]["Immediate"] == 1
        assert snapshot["counts"]["Minor"] == 1
        assert snapshot["groups"]["Immediate"][0]["flags"][0]["label"] == "High RR"
        assert snapshot["resources"]["equipment"][0] == {"resource_type": "ICU bed", "quantity": 2}
        assert snapshot["resources"]["personnel"][0] == {"resource_type": "Nurse", "quantity": 4}

    def test_agent_summary_keeps_flags_patient_specific(self):
        snapshot = build_acuity_snapshot(
            patient_rows=[
                {
                    "id": 7,
                    "chief_complaint": "Chest pain",
                    "heart_rate": 130,
                    "able_to_walk": False,
                    "spontaneous_breathing": True,
                    "respiratory_rate": 34,
                    "radial_pulse_present": False,
                    "obeys_commands": True,
                    "triage_category": "Immediate",
                },
            ],
            resource_rows=[],
        )

        summary = summarize_patient_cards_for_agent(snapshot["groups"]["Immediate"])

        assert "#7 Chest pain (HR 130, RR 34)" in summary
        assert "Non-ambulatory" in summary
        assert "High HR" in summary
        assert "High RR" in summary
        assert "Perfusion" in summary

    def test_snapshot_does_not_invent_resource_recommendations(self):
        snapshot = build_acuity_snapshot(
            patient_rows=[
                {
                    "id": 1,
                    "chief_complaint": "Respiratory distress",
                    "heart_rate": 118,
                    "able_to_walk": False,
                    "spontaneous_breathing": True,
                    "respiratory_rate": 34,
                    "radial_pulse_present": True,
                    "obeys_commands": True,
                    "triage_category": "Immediate",
                },
            ],
            resource_rows=[
                {"resource_type": "Ventilator", "quantity": 1, "status": "available"},
            ],
        )

        card = snapshot["groups"]["Immediate"][0]
        assert "resource_recommendations" not in card
        assert "availability_limited_needs" not in card

    def test_agent_full_board_summary_includes_all_categories(self):
        snapshot = build_acuity_snapshot(
            patient_rows=[
                {
                    "id": 1,
                    "chief_complaint": "Respiratory distress",
                    "heart_rate": 118,
                    "able_to_walk": False,
                    "spontaneous_breathing": True,
                    "respiratory_rate": 34,
                    "radial_pulse_present": True,
                    "obeys_commands": True,
                    "triage_category": "Immediate",
                },
                {
                    "id": 2,
                    "chief_complaint": "Wrist pain",
                    "heart_rate": 82,
                    "able_to_walk": True,
                    "spontaneous_breathing": True,
                    "respiratory_rate": 16,
                    "radial_pulse_present": True,
                    "obeys_commands": True,
                    "triage_category": "Minor",
                },
            ],
            resource_rows=[],
        )

        summary = summarize_full_acuity_board_for_agent(snapshot)

        assert "Immediate: #1 Respiratory distress" in summary
        assert "Minor: #2 Wrist pain" in summary

# ---------------------------------------------------------------- parse_patient_workbook

class TestParsePatientWorkbook:
    def test_parse_excel_workbook_is_generic(self):
        content = _make_workbook_bytes([
            ("ICU bed", 2, "available"),
        ], headers=["resource_type", "quantity", "status"])
        [sheet] = parse_excel_workbook(content)
        assert sheet["headers"] == ["resource_type", "quantity", "status"]
        assert sheet["rows"][0]["resource_type"] == "ICU bed"

    def test_parses_valid_rows(self):
        content = _make_workbook_bytes([
            ("Laceration, forearm", 88, True, True, 16, True, True),
            ("Crush injury, leg", 110, False, True, 34, True, True),
        ])
        records = parse_patient_workbook(content)
        assert len(records) == 2
        assert records[0] == {
            "chief_complaint": "Laceration, forearm",
            "heart_rate": 88,
            "able_to_walk": True,
            "spontaneous_breathing": True,
            "respiratory_rate": 16,
            "radial_pulse_present": True,
            "obeys_commands": True,
        }
        assert records[1]["respiratory_rate"] == 34

    def test_coerces_string_booleans(self):
        content = _make_workbook_bytes([
            ("Test", 90, "yes", "TRUE", 20, "1", "no"),
        ])
        [record] = parse_patient_workbook(content)
        assert record["able_to_walk"] is True
        assert record["spontaneous_breathing"] is True
        assert record["radial_pulse_present"] is True
        assert record["obeys_commands"] is False

    def test_missing_bool_raises_value_error(self):
        content = _make_workbook_bytes([
            ("Test", 90, None, None, 20, None, None),
        ])
        with pytest.raises(ValueError, match="Row 2: missing boolean value"):
            parse_patient_workbook(content)

    def test_invalid_or_missing_ints_raise_value_error(self):
        content = _make_workbook_bytes([
            ("Test", "not-a-number", True, True, None, True, True),
        ])
        with pytest.raises(ValueError, match="Row 2: invalid integer value"):
            parse_patient_workbook(content)

    def test_out_of_range_vitals_raise_value_error(self):
        content = _make_workbook_bytes([
            ("Test", 251, True, True, 81, True, True),
        ])
        with pytest.raises(ValueError, match="heart_rate must be between 0 and 250"):
            parse_patient_workbook(content)

    def test_duplicate_patient_rows_raise_value_error(self):
        row = ("Test", 90, True, True, 20, True, True)
        content = _make_workbook_bytes([row, row])
        with pytest.raises(ValueError, match="Row 3: duplicate patient record"):
            parse_patient_workbook(content)

    def test_skips_fully_blank_rows(self):
        content = _make_workbook_bytes([
            ("Real patient", 90, True, True, 16, True, True),
            (None, None, None, None, None, None, None),
        ])
        records = parse_patient_workbook(content)
        assert len(records) == 1
        assert records[0]["chief_complaint"] == "Real patient"

    def test_empty_file_raises_value_error(self):
        content = _make_workbook_bytes([], headers=None)
        with pytest.raises(ValueError, match="Empty file"):
            parse_patient_workbook(content)

    def test_headers_only_no_data_raises_value_error(self):
        content = _make_workbook_bytes([])
        with pytest.raises(ValueError, match="No patient rows found"):
            parse_patient_workbook(content)

    def test_garbage_bytes_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not read file"):
            parse_patient_workbook(b"this is not an excel file at all")

    def test_stages_workbook_without_committing(self):
        sheets = [{
            "name": "Resources",
            "headers": ["resource_type", "quantity", "status"],
            "row_count": 1,
            "rows": [{"resource_type": "ICU bed", "quantity": 2, "status": "available", "_row_number": 2}],
        }]
        upload_id = stage_workbook_attachment("resources.xlsx", sheets)
        assert PENDING_ATTACHMENTS[upload_id]["sheets"] == sheets

    def test_attachment_context_contains_generic_workbook(self):
        sheets = [{
            "name": "Patients",
            "headers": HEADERS,
            "row_count": 1,
            "rows": [{
                "chief_complaint": "Chest pain",
                "heart_rate": 130,
                "able_to_walk": False,
                "spontaneous_breathing": True,
                "respiratory_rate": 22,
                "radial_pulse_present": False,
                "obeys_commands": True,
                "_row_number": 2,
            }],
        }]
        text = format_workbook_attachment_context(
            upload_id="upload-1",
            filename="patients.xlsx",
            sheets=sheets,
            user_text="Review this file.",
        )
        payload = json.loads(text.split("ATTACHED_FILE_CONTEXT:\n", 1)[1])
        assert text.startswith("Review this file.")
        assert payload["type"] == "excel_workbook_attachment"
        assert payload["upload_id"] == "upload-1"
        assert payload["sheets"] == sheets
