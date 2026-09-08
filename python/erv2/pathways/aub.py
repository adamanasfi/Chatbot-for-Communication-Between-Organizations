"""
Abnormal Uterine Bleeding (AUB) -- Nationwide Children's Hospital ED clinical
pathway, page-1 main algorithm. Published 6/19/2024.

NOT yet encoded: the detailed sub-plans on pages 7-10 (exact medication
tapering schedules, the PICU-admission sub-decision, weight-based iron
dosing). Those terminal nodes carry a one-line disposition rather than
further decision branches -- encoding them is the same pattern, just more
nodes, deferred until this shape is confirmed.
"""
from .schema import Pathway, PathwayNode

AUB_PATHWAY = Pathway(
    id="abnormal_uterine_bleeding",
    name="Abnormal Uterine Bleeding (AUB)",
    chief_complaint_keywords=["abnormal uterine bleeding", "heavy menstrual", "vaginal bleeding", "aub"],
    start_node="pregnancy_test",
    nodes={
        "pregnancy_test": PathwayNode(
            id="pregnancy_test", kind="decision", label="Pregnancy test positive?",
            x=400, y=0, question="Is the pregnancy test positive?",
            branches={"yes": "off_pathway", "no": "signs_of_shock"},
        ),
        "off_pathway": PathwayNode(
            id="off_pathway", kind="terminal", label="Off Pathway", x=680, y=0,
            disposition="Off pathway -- facilitate transfer to adult facility.",
        ),
        "signs_of_shock": PathwayNode(
            id="signs_of_shock", kind="decision", label="Signs of shock?",
            x=400, y=120, question="Are there signs of shock?",
            branches={"yes": "shock_management", "no": "initial_labs"},
        ),
        "shock_management": PathwayNode(
            id="shock_management", kind="action", label="Shock management", x=680, y=120,
            labs=["CBC", "Type & Screen", "PT/PTT", "Fibrinogen"],
            medications=["IV conjugated estrogen", "Consider PRBC infusion"],
            resource_needs=["IV access", "IVF bolus"],
            next_node="severe_bleeding_admission",
        ),
        "severe_bleeding_admission": PathwayNode(
            id="severe_bleeding_admission", kind="terminal", label="Severe Bleeding Admission Plan",
            x=680, y=306, disposition="Severe Bleeding Admission Plan.",
        ),
        "initial_labs": PathwayNode(
            id="initial_labs", kind="action", label="Initial labs", x=400, y=240,
            labs=["CBC with differential", "STI testing (GC/Chlamydia/Trich) if sexually active"],
            next_node="assess_severity",
        ),
        "assess_severity": PathwayNode(
            id="assess_severity", kind="decision", label="Assess bleeding severity",
            x=400, y=394, question="Mild, moderate, or severe bleeding?",
            branches={"mild": "mild_discharge", "moderate": "moderate_discharge", "severe": "severe_hgb_check"},
            option_details={
                "mild": (
                    "Slightly prolonged menses; slightly more frequent cycle; "
                    "increased flow; Hgb normal."
                ),
                "moderate": (
                    "Menses lasting >7 days OR cycle frequency <3 weeks, "
                    "AND Hgb 10-11 g/dL."
                ),
                "severe": (
                    "Menstrual cycles with heavy bleeding that disrupt activities "
                    "of daily living, AND Hgb <10 g/dL."
                ),
            },
        ),
        "mild_discharge": PathwayNode(
            id="mild_discharge", kind="terminal", label="Mild Bleeding Discharge",
            x=130, y=514, disposition="Mild Bleeding Discharge Plan.",
        ),
        "moderate_discharge": PathwayNode(
            id="moderate_discharge", kind="terminal", label="Moderate Bleeding Discharge",
            x=400, y=514, disposition="Moderate Bleeding Discharge Plan.",
        ),
        "severe_hgb_check": PathwayNode(
            id="severe_hgb_check", kind="decision", label="Hgb ≤ 7 g/dL or symptomatic anemia?",
            x=670, y=514, question="Is Hgb ≤ 7 g/dL and/or is the patient symptomatic from anemia?",
            branches={"yes": "severe_admit", "no": "severe_discharge"},
        ),
        "severe_discharge": PathwayNode(
            id="severe_discharge", kind="terminal", label="Severe Bleeding Discharge",
            x=520, y=634, disposition="Severe Bleeding Discharge Plan.",
        ),
        "severe_admit": PathwayNode(
            id="severe_admit", kind="terminal", label="Severe Bleeding Admit",
            x=780, y=634, disposition="Severe Bleeding Admission Plan.",
        ),
    },
)
