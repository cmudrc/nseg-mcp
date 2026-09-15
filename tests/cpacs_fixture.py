"""A small CPACS document for the adapter tests.

Numbers are D150-like: reference area, a polar CD = CD0 + k*CL^2 that the
adapter fits from one (CL, CD, CD0) point, and a mass-based TSFC as the
propulsion stage supplies. Nothing here is read from a restricted file.
"""

from __future__ import annotations

REF_AREA_M2 = 122.4
CL, CD0, K = 0.5, 0.0164, 0.0398
CD = CD0 + K * CL * CL
TSFC_1_PER_S = 1.7e-5
FN_N = 120_000.0
MTOM_KG = 78_126.0
WEIGHT_KG = 70_000.0

EXISTING_UPDATES = ("First release.", "Second release.")


def make_cpacs(
    *,
    mtom_kg: float | None = None,
    with_header: bool = True,
    with_updates: bool = True,
) -> str:
    updates = ""
    if with_updates:
        entries = "".join(
            f"<update><modification>{text}</modification><creator>tests</creator>"
            f"<timestamp>202{i}-01-01T00:00:00</timestamp><version>0.{i + 1}</version>"
            "<cpacsVersion>3.2</cpacsVersion></update>"
            for i, text in enumerate(EXISTING_UPDATES)
        )
        updates = f"<updates>{entries}</updates>"

    header = ""
    if with_header:
        header = (
            "<header><name>test aircraft</name><description>adapter test fixture</description>"
            "<creator>tests</creator><timestamp>2020-01-01T00:00:00</timestamp>"
            f"<version>0.2</version><cpacsVersion>3.2</cpacsVersion>{updates}</header>"
        )

    masses = ""
    if mtom_kg is not None:
        masses = (
            "<analyses><massBreakdown><designMasses>"
            f"<mTOM><mass>{mtom_kg}</mass></mTOM>"
            "</designMasses></massBreakdown></analyses>"
        )

    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        f"<cpacs>{header}<vehicles><aircraft><model uID='test'><name>test</name>"
        f"<reference><area>{REF_AREA_M2}</area></reference>{masses}"
        "<analysisResults><aero><coefficients>"
        f"<CL>{CL}</CL><CD>{CD}</CD><CD0>{CD0}</CD0>"
        "</coefficients></aero></analysisResults></model></aircraft>"
        "<engines><engine uID='e'><analysis><mcpResults>"
        f"<TSFC_1_per_s>{TSFC_1_PER_S}</TSFC_1_per_s><Fn_N>{FN_N}</Fn_N>"
        "</mcpResults></analysis></engine></engines></vehicles></cpacs>"
    )
