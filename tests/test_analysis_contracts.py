import unittest

from core.compute import compute_metrics
from core.analysis_contracts import (
    InputContract,
    CalculatedMetrics,
    build_calculated_metrics,
    farge_for_cashflow,
    farge_for_roe,
    farge_for_break_even_gap,
    beregn_score_og_dom,
    DecisionVerdict,
    build_decision_result,
    DecisionResult,
    map_decision_to_ui,
    calc_risk_score,
    calc_total_score,
)


class BuildCalculatedMetricsTests(unittest.TestCase):
    def test_matches_legacy_compute_values(self) -> None:
        input_contract = InputContract(
            kjopesum=4_000_000.0,
            egenkapital=800_000.0,
            rente_pct_pa=5.25,
            lanetid_ar=25,
            brutto_leie_mnd=18_500.0,
            felleskost_mnd=3_200.0,
            vedlikehold_pct_av_leie=7.5,
            andre_kost_mnd=1_200.0,
        )

        result = build_calculated_metrics(input_contract)

        expected = compute_metrics(
            price=input_contract.kjopesum,
            equity=input_contract.egenkapital,
            interest=input_contract.rente_pct_pa,
            term_years=input_contract.lanetid_ar,
            rent=input_contract.brutto_leie_mnd,
            hoa=input_contract.felleskost_mnd,
            maint_pct=input_contract.vedlikehold_pct_av_leie,
            vacancy_pct=0.0,
            other_costs=input_contract.andre_kost_mnd,
        )

        self.assertIsInstance(result, CalculatedMetrics)
        self.assertEqual(result.cashflow_mnd, expected["cashflow"])
        self.assertEqual(result.break_even_leie_mnd, expected["break_even"])
        self.assertEqual(result.noi_aar, expected["noi_year"])
        self.assertEqual(result.roe_pct, expected["total_equity_return_pct"])
        self.assertEqual(result.lanekost_mnd, expected["m_payment"])
        self.assertEqual(
            result.aarlig_nedbetaling_lan,
            expected["principal_reduction_year"],
        )


class FargekoderTests(unittest.TestCase):
    def test_cashflow_thresholds(self) -> None:
        self.assertEqual(farge_for_cashflow(-2001), "red")
        self.assertEqual(farge_for_cashflow(-2000), "orange")
        self.assertEqual(farge_for_cashflow(0), "orange")
        self.assertEqual(farge_for_cashflow(1), "green")

    def test_roe_thresholds(self) -> None:
        self.assertEqual(farge_for_roe(4.99), "red")
        self.assertEqual(farge_for_roe(5.0), "orange")
        self.assertEqual(farge_for_roe(9.99), "orange")
        self.assertEqual(farge_for_roe(10.0), "green")

    def test_break_even_gap_thresholds(self) -> None:
        faktisk_leie = 20000.0
        terskel = faktisk_leie * 0.05

        self.assertEqual(
            farge_for_break_even_gap(faktisk_leie, faktisk_leie + terskel + 1),
            "red",
        )
        self.assertEqual(
            farge_for_break_even_gap(faktisk_leie, faktisk_leie + terskel),
            "orange",
        )
        self.assertEqual(
            farge_for_break_even_gap(faktisk_leie, faktisk_leie - terskel),
            "orange",
        )
        self.assertEqual(
            farge_for_break_even_gap(faktisk_leie, faktisk_leie - terskel - 1),
            "green",
        )


class ScoringTests(unittest.TestCase):
    def test_klar_gronn_dom(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=1_500.0,
            break_even_leie_mnd=15_000.0,
            noi_aar=180_000.0,
            roe_pct=12.0,
            lanekost_mnd=9_000.0,
            aarlig_nedbetaling_lan=80_000.0,
        )
        contract = InputContract(
            kjopesum=5_000_000.0,
            egenkapital=1_000_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=18_000.0,
            felleskost_mnd=3_000.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_000.0,
        )

        score, dom, econ_score, used_cap = beregn_score_og_dom(
            metrics,
            contract,
            risk_score=100,
            has_tg_data=True,
        )

        self.assertEqual(score, 100)
        self.assertEqual(dom, DecisionVerdict.BRA)
        self.assertEqual(econ_score, 100)
        self.assertFalse(used_cap)

    def test_svak_gronn_dom(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=1_200.0,
            break_even_leie_mnd=19_100.0,
            noi_aar=150_000.0,
            roe_pct=8.5,
            lanekost_mnd=10_000.0,
            aarlig_nedbetaling_lan=70_000.0,
        )
        contract = InputContract(
            kjopesum=4_800_000.0,
            egenkapital=960_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=20_000.0,
            felleskost_mnd=3_500.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_200.0,
        )

        score, dom, econ_score, used_cap = beregn_score_og_dom(
            metrics,
            contract,
            risk_score=100,
            has_tg_data=True,
        )

        self.assertEqual(score, 85)
        self.assertEqual(dom, DecisionVerdict.BRA)
        self.assertEqual(econ_score, 78)
        self.assertFalse(used_cap)

    def test_gul_dom(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=800.0,
            break_even_leie_mnd=20_500.0,
            noi_aar=120_000.0,
            roe_pct=8.0,
            lanekost_mnd=11_000.0,
            aarlig_nedbetaling_lan=60_000.0,
        )
        contract = InputContract(
            kjopesum=4_500_000.0,
            egenkapital=900_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=20_000.0,
            felleskost_mnd=3_200.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_100.0,
        )

        score, dom, econ_score, used_cap = beregn_score_og_dom(
            metrics,
            contract,
            risk_score=100,
            has_tg_data=True,
        )

        self.assertEqual(score, 71)
        self.assertEqual(dom, DecisionVerdict.OK)
        self.assertEqual(econ_score, 58)
        self.assertFalse(used_cap)

    def test_rod_dom(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=-2_500.0,
            break_even_leie_mnd=22_000.0,
            noi_aar=80_000.0,
            roe_pct=5.0,
            lanekost_mnd=12_000.0,
            aarlig_nedbetaling_lan=40_000.0,
        )
        contract = InputContract(
            kjopesum=4_200_000.0,
            egenkapital=840_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=20_000.0,
            felleskost_mnd=3_000.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_000.0,
        )

        score, dom, econ_score, used_cap = beregn_score_og_dom(
            metrics,
            contract,
            risk_score=100,
            has_tg_data=True,
        )

        self.assertEqual(score, 30)
        self.assertEqual(dom, DecisionVerdict.DAARLIG)
        self.assertEqual(econ_score, 0)
        self.assertFalse(used_cap)


class DecisionResultTests(unittest.TestCase):
    def test_negative_cashflow_generates_expected_actions(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=-1500.0,
            break_even_leie_mnd=22_000.0,
            noi_aar=120_000.0,
            roe_pct=7.5,
            lanekost_mnd=11_000.0,
            aarlig_nedbetaling_lan=55_000.0,
        )
        contract = InputContract(
            kjopesum=4_500_000.0,
            egenkapital=900_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=20_000.0,
            felleskost_mnd=3_200.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_000.0,
        )

        result = build_decision_result(contract, metrics)

        self.assertIsInstance(result, DecisionResult)
        self.assertEqual(
            result.status_setning,
            "Marginal lønnsomhet. Cashflow negativ, men kan bedres med tiltak.",
        )
        self.assertIn("Forhandle pris ned", result.tiltak)
        self.assertIn("Vurder moderat leieøkning", result.tiltak)
        self.assertIn("Senk driftskostnader/felleskost om mulig", result.tiltak)
        self.assertIn(
            "Øk leie eller redusér EK-binding/forhandle rente",
            result.tiltak,
        )
        self.assertLessEqual(len(result.tiltak), 4)
        self.assertEqual(result.positivt, [])
        self.assertEqual(result.risiko, [])
        self.assertEqual(result.nokkel_tall[0].farge, "orange")
        self.assertTrue(result.nokkel_tall[0].verdi.endswith(" kr/mnd"))
        self.assertEqual(result.score_0_100, 35)
        self.assertEqual(result.dom, DecisionVerdict.DAARLIG)
        self.assertIsNone(result.dom_notat)

    def test_positive_cashflow_high_roe_adds_positive_bullets(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=2_500.0,
            break_even_leie_mnd=15_000.0,
            noi_aar=180_000.0,
            roe_pct=12.5,
            lanekost_mnd=8_500.0,
            aarlig_nedbetaling_lan=85_000.0,
        )
        contract = InputContract(
            kjopesum=5_200_000.0,
            egenkapital=1_040_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=20_000.0,
            felleskost_mnd=3_200.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_100.0,
        )

        result = build_decision_result(contract, metrics)

        self.assertEqual(
            result.status_setning,
            "Solid lønnsomhet med håndterbare forutsetninger.",
        )
        self.assertEqual(result.tiltak, [])
        self.assertIn("Positiv månedlig cashflow", result.positivt)
        self.assertIn("Sterk avkastning på egenkapital", result.positivt)
        self.assertIn("God buffer mot nullpunkt", result.positivt)
        self.assertLessEqual(len(result.positivt), 4)
        self.assertEqual(result.nokkel_tall[-1].farge, "green")
        self.assertTrue(result.nokkel_tall[-1].verdi.endswith(" %"))
        self.assertEqual(result.score_0_100, 74)
        self.assertEqual(result.dom, DecisionVerdict.OK)
        self.assertEqual(
            result.dom_notat,
            "Dom basert på økonomi. Teknisk risiko ikke vurdert ennå.",
        )
        self.assertEqual(result.risiko, [])

    def test_includes_tg_findings_and_uses_risk_score(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=1_200.0,
            break_even_leie_mnd=19_100.0,
            noi_aar=150_000.0,
            roe_pct=8.5,
            lanekost_mnd=10_000.0,
            aarlig_nedbetaling_lan=70_000.0,
        )
        contract = InputContract(
            kjopesum=4_800_000.0,
            egenkapital=960_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=20_000.0,
            felleskost_mnd=3_500.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_200.0,
        )

        result = build_decision_result(
            contract,
            metrics,
            tg2_items=["Normal slitasje på kjøkken"],
            tg3_items=["Fuktskade i kjeller"],
            tg_data_available=True,
        )

        self.assertEqual(result.score_0_100, 80)
        self.assertEqual(result.dom, DecisionVerdict.BRA)
        self.assertEqual(result.dom_notat, None)
        self.assertIn("TG3: Fuktskade i kjeller", result.risiko)
        self.assertIn("TG2: Normal slitasje på kjøkken", result.risiko)


class DecisionResultMapperTests(unittest.TestCase):
    def test_mapper_returns_complete_dataset(self) -> None:
        metrics = CalculatedMetrics(
            cashflow_mnd=1_500.0,
            break_even_leie_mnd=15_000.0,
            noi_aar=180_000.0,
            roe_pct=12.0,
            lanekost_mnd=9_000.0,
            aarlig_nedbetaling_lan=80_000.0,
        )
        contract = InputContract(
            kjopesum=5_000_000.0,
            egenkapital=1_000_000.0,
            rente_pct_pa=5.0,
            lanetid_ar=25,
            brutto_leie_mnd=18_000.0,
            felleskost_mnd=3_000.0,
            vedlikehold_pct_av_leie=6.0,
            andre_kost_mnd=1_000.0,
        )

        decision = build_decision_result(contract, metrics)
        data = map_decision_to_ui(decision)

        self.assertEqual(set(data.keys()), {
            "status",
            "tiltak",
            "positivt",
            "risiko",
            "nokkel_tall",
            "scorelinjal",
            "dom_notat",
        })
        self.assertEqual(data["status"]["score"], decision.score_0_100)
        self.assertEqual(data["status"]["dom"], decision.dom.value)
        self.assertEqual(data["scorelinjal"]["value"], decision.score_0_100)
        self.assertEqual(data["scorelinjal"]["farge"], "orange")
        self.assertEqual(len(data["nokkel_tall"]), len(decision.nokkel_tall))
        self.assertEqual(data["dom_notat"], decision.dom_notat)

    def test_scorelinjal_color_mapping(self) -> None:
        decision = DecisionResult(
            score_0_100=45,
            dom=DecisionVerdict.DAARLIG,
            status_setning="",
            tiltak=[],
            positivt=[],
            risiko=[],
            nokkel_tall=[],
        )
        data = map_decision_to_ui(decision)
        self.assertEqual(data["scorelinjal"]["farge"], "red")

        decision_ok = decision.model_copy(update={
            "dom": DecisionVerdict.OK,
            "score_0_100": 60,
        })
        data_ok = map_decision_to_ui(decision_ok)
        self.assertEqual(data_ok["scorelinjal"]["farge"], "orange")

        decision_good = decision.model_copy(update={
            "dom": DecisionVerdict.BRA,
            "score_0_100": 85,
        })
        data_good = map_decision_to_ui(decision_good)
        self.assertEqual(data_good["scorelinjal"]["farge"], "green")


class RiskScoreTests(unittest.TestCase):
    def test_no_tg_data_returns_default(self) -> None:
        self.assertEqual(calc_risk_score([], []), 60)

    def test_only_tg3(self) -> None:
        self.assertEqual(calc_risk_score([], ["punkt"]), 88)

    def test_only_tg2_with_cap(self) -> None:
        self.assertEqual(calc_risk_score(["a", "b"], [], has_tg_data=True), 90)

    def test_mixed_tg_with_caps(self) -> None:
        self.assertEqual(
            calc_risk_score(["a", "b", "c", "d"], ["x"], has_tg_data=True),
            73,
        )

    def test_no_findings_but_data_available_gives_perfect(self) -> None:
        self.assertEqual(calc_risk_score([], [], has_tg_data=True), 100)


class TotalScoreTests(unittest.TestCase):
    def test_combined_score_with_tg_data(self) -> None:
        self.assertEqual(calc_total_score(80, 100, True), 86)

    def test_combined_score_with_weights(self) -> None:
        self.assertEqual(calc_total_score(60, 74, True), 65)

    def test_cap_when_no_tg_data(self) -> None:
        self.assertEqual(calc_total_score(95, 100, False), 74)


if __name__ == "__main__":
    unittest.main()
