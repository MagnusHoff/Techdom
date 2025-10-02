import pytest

from techdom.domain.analysis_service import (
    AnalysisDecisionContext,
    compute_analysis,
    input_contract_from_params,
)
from techdom.processing.ai import ai_explain
from techdom.processing.compute import compute_metrics
from techdom.domain.analysis_contracts import (
    build_calculated_metrics,
    build_decision_result,
    map_decision_to_ui,
)


@pytest.mark.parametrize(
    "price, equity",
    [
        ("4\u00a0500\u00a0000", "675\u00a0000"),
        (4_500_000, 675_000),
    ],
)
def test_compute_analysis_matches_legacy_flow(monkeypatch, price, equity):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    params = {
        "price": price,
        "equity": equity,
        "interest": "5,1",
        "term_years": "30",
        "rent": "18\u00a0000",
        "hoa": "3\u00a0000",
        "maint_pct": "6,0",
        "vacancy_pct": 0,
        "other_costs": "800",
    }
    ctx = AnalysisDecisionContext(
        tg2_items=["TG2 punkt"],
        tg3_items=["TG3 funn"],
        tg_data_available=True,
    )

    result = compute_analysis(params, ctx)

    expected_metrics = compute_metrics(
        4_500_000,
        675_000,
        5.1,
        30,
        18_000,
        3_000,
        6.0,
        0.0,
        800,
    )
    contract = input_contract_from_params(params)
    calculated_metrics = build_calculated_metrics(contract)
    expected_decision = build_decision_result(
        contract,
        calculated_metrics,
        tg2_items=list(ctx.tg2_items),
        tg3_items=list(ctx.tg3_items),
        tg_data_available=ctx.tg_data_available,
    )
    expected_ui = map_decision_to_ui(expected_decision)
    expected_ai = ai_explain(
        {
            "price": 4_500_000,
            "equity": 675_000,
            "interest": 5.1,
            "term_years": 30,
            "rent": 18_000,
            "hoa": 3_000,
        },
        expected_metrics,
    )

    assert result.metrics == expected_metrics
    assert result.calculated_metrics == calculated_metrics
    assert result.decision_result == expected_decision
    assert result.decision_ui == expected_ui
    assert result.ai_text == expected_ai
