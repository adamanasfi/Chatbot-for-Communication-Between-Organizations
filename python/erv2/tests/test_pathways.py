"""
Unit tests for the clinical pathway engine -- pure function, no database.
"""
import pytest

from pathways import run_pathway
from pathways.aub import AUB_PATHWAY


class TestRunPathway:
    def test_no_answers_yet_stops_at_first_question(self):
        result = run_pathway(AUB_PATHWAY, {})
        assert result["visited_nodes"] == ["pregnancy_test"]
        assert result["complete"] is False
        assert result["pending_node"]["id"] == "pregnancy_test"
        assert result["pending_node"]["options"] == ["yes", "no"]

    def test_pregnancy_positive_goes_off_pathway(self):
        result = run_pathway(AUB_PATHWAY, {"pregnancy_test": "yes"})
        assert result["complete"] is True
        assert result["visited_nodes"] == ["pregnancy_test", "off_pathway"]
        assert "transfer to adult facility" in result["disposition"]
        assert result["labs"] == []
        assert result["resource_needs"] == []

    def test_shock_path_accumulates_action_node_effects(self):
        result = run_pathway(AUB_PATHWAY, {
            "pregnancy_test": "no",
            "signs_of_shock": "yes",
        })
        assert result["complete"] is True
        assert result["visited_nodes"] == [
            "pregnancy_test", "signs_of_shock", "shock_management", "severe_bleeding_admission",
        ]
        assert result["labs"] == ["CBC", "Type & Screen", "PT/PTT", "Fibrinogen"]
        assert result["medications"] == ["IV conjugated estrogen", "Consider PRBC infusion"]
        assert result["resource_needs"] == ["IV access", "IVF bolus"]
        assert result["disposition"] == "Severe Bleeding Admission Plan."

    def test_no_shock_runs_initial_labs_then_stops_at_severity(self):
        result = run_pathway(AUB_PATHWAY, {
            "pregnancy_test": "no",
            "signs_of_shock": "no",
        })
        assert result["complete"] is False
        assert result["visited_nodes"] == ["pregnancy_test", "signs_of_shock", "initial_labs", "assess_severity"]
        assert result["labs"] == ["CBC with differential", "STI testing (GC/Chlamydia/Trich) if sexually active"]
        assert result["pending_node"]["id"] == "assess_severity"
        assert set(result["pending_node"]["options"]) == {"mild", "moderate", "severe"}

    def test_severity_options_carry_protocol_criteria_as_reminders(self):
        result = run_pathway(AUB_PATHWAY, {
            "pregnancy_test": "no",
            "signs_of_shock": "no",
        })
        details = result["pending_node"]["option_details"]
        assert "Hgb normal" in details["mild"]
        assert "Hgb 10-11 g/dL" in details["moderate"]
        assert "Hgb <10 g/dL" in details["severe"]

    def test_pending_node_with_no_option_details_reports_empty_dict(self):
        result = run_pathway(AUB_PATHWAY, {})
        assert result["pending_node"]["option_details"] == {}

    def test_mild_severity_reaches_mild_discharge(self):
        result = run_pathway(AUB_PATHWAY, {
            "pregnancy_test": "no",
            "signs_of_shock": "no",
            "assess_severity": "mild",
        })
        assert result["complete"] is True
        assert result["disposition"] == "Mild Bleeding Discharge Plan."

    def test_severe_with_low_hgb_reaches_admit(self):
        result = run_pathway(AUB_PATHWAY, {
            "pregnancy_test": "no",
            "signs_of_shock": "no",
            "assess_severity": "severe",
            "severe_hgb_check": "yes",
        })
        assert result["complete"] is True
        assert result["visited_nodes"][-1] == "severe_admit"
        assert result["disposition"] == "Severe Bleeding Admission Plan."

    def test_severe_without_low_hgb_reaches_discharge(self):
        result = run_pathway(AUB_PATHWAY, {
            "pregnancy_test": "no",
            "signs_of_shock": "no",
            "assess_severity": "severe",
            "severe_hgb_check": "no",
        })
        assert result["complete"] is True
        assert result["visited_nodes"][-1] == "severe_discharge"
        assert result["disposition"] == "Severe Bleeding Discharge Plan."

    def test_invalid_answer_raises_value_error(self):
        with pytest.raises(ValueError, match="no branch for answer 'maybe'"):
            run_pathway(AUB_PATHWAY, {"pregnancy_test": "maybe"})

    def test_stale_answer_at_unreached_node_is_ignored(self):
        # An answer recorded for a node the walk never reaches (e.g. from a
        # different branch) must not affect the result -- only nodes actually
        # visited should matter.
        result = run_pathway(AUB_PATHWAY, {
            "pregnancy_test": "yes",
            "signs_of_shock": "yes",  # irrelevant once off_pathway is reached
        })
        assert result["visited_nodes"] == ["pregnancy_test", "off_pathway"]
