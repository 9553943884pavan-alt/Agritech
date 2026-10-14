import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import main


class MockResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class OptimizeFarmTests(unittest.TestCase):
    def test_app_lifespan_initializes_the_price_cache(self):
        with patch("backend.main.initialize_price_cache") as initialize:
            async def run_lifespan():
                async with main.lifespan(main.app):
                    initialize.assert_called_once_with()

            asyncio.run(run_lifespan())

    def setUp(self):
        self.geocoding = {
            "results": [
                {
                    "name": "Nizamabad",
                    "country_code": "IN",
                    "admin1": "Telangana",
                    "admin2": "Nizamabad",
                    "latitude": 18.67,
                    "longitude": 78.09,
                }
            ]
        }
        self.weather = {
            "hourly": {
                "temperature_2m": [28.0, 30.0],
                "relative_humidity_2m": [60.0, 80.0],
            },
            "daily": {"precipitation_sum": [4.0, 6.0]},
        }
        self.soil_summary = {"cycle": "2025-26", "district": "Nizamabad"}

    def test_classifier_reasoning_is_additive_to_each_top_crop_score(self):
        crops, reasoning = main._predict_crop_recommendations(
            n=80,
            p=40,
            k=45,
            temperature=28,
            humidity=60,
            ph=6.5,
            rainfall=900,
        )

        self.assertEqual([item["crop"] for item in reasoning["crops"]], crops)
        self.assertEqual(
            [item["rank"] for item in reasoning["crops"]],
            list(range(1, len(crops) + 1)),
        )
        self.assertEqual(reasoning["method"], "random_forest_shap")
        for crop in reasoning["crops"]:
            reconstructed_score = crop["baseline_score"] + sum(
                item["shap_value"] for item in crop["feature_contributions"]
            )
            self.assertAlmostEqual(reconstructed_score, crop["score"], places=6)

    def test_classifier_does_not_recommend_classes_with_zero_probability(self):
        crops, reasoning = main._predict_crop_recommendations(
            n=85,
            p=42,
            k=40,
            temperature=25,
            humidity=76.33,
            ph=6.8,
            rainfall=420.1,
        )

        self.assertEqual(crops, ["rice", "jute"])
        self.assertTrue(all(crop["score"] > 0 for crop in reasoning["crops"]))

    def test_gemini_reasoning_uses_key_and_validates_expected_crop_answers(self):
        generated = {
            "classifier_summary": "The ranking follows the supplied model scores and SHAP evidence.",
            "classifier_reasons": [
                {"crop": crop, "reason": f"Evidence for {crop}."}
                for crop in ("rice", "jute", "maize")
            ],
            "optimizer_summary": "The plan uses the available land and budget limits.",
            "allocation_reasons": [{"crop": "Rice", "reason": "Positive estimated margin."}],
        }
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "test-model"}),
            patch(
                "backend.main.urllib.request.urlopen",
                return_value=MockResponse({
                    "candidates": [{
                        "content": {"parts": [{"text": json.dumps(generated)}]},
                    }],
                }),
            ) as urlopen,
        ):
            result = main._generate_ai_reasoning(
                farmer_inputs={"soil": {"N": 80}},
                classifier_reasoning={
                    "method": "random_forest_shap",
                    "crops": [{"feature_contributions": [{"shap_value": 0.2}]}],
                },
                optimizer_reasoning={"crops": [{"crop": "Rice"}]},
                optimization_result={"allocations": [{"crop": "Rice"}]},
                recommended_crops=["rice", "jute", "maize"],
                optimized_crops=["rice"],
                crop_economics={"rice": {"price_rs_per_quintal": 3000}},
            )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(len(result["classifier_reasons"]), 3)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertEqual(
            request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent",
        )
        prompt = json.loads(request.data)["contents"][0]["parts"][0]["text"]
        self.assertIn('"shap_value": 0.2', prompt)
        self.assertNotIn("test-key", prompt)

    def test_gemini_reasoning_reports_missing_api_key_without_network_request(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": ""}),
            patch("backend.main.urllib.request.urlopen") as urlopen,
        ):
            result = main._generate_ai_reasoning(
                farmer_inputs={},
                classifier_reasoning={},
                optimizer_reasoning={},
                optimization_result={},
                recommended_crops=[],
                optimized_crops=[],
                crop_economics={},
            )

        self.assertEqual(result, {
            "status": "not_configured",
            "error_code": "missing_api_key",
        })
        urlopen.assert_not_called()

    def test_optimizer_reasoning_reports_the_same_per_acre_economics(self):
        with patch(
            "backend.optimizer.lp_solver.load_universal_economics",
            return_value={"rice": {"cost_per_acre": 10000}},
        ):
            result = main.optimize_crop_allocation(
                land_acres=2,
                budget=100000,
                candidate_crops=["rice"],
                custom_yields={"rice": 4.942},
                custom_prices={"rice": 3000},
            )

        crop = result.reasoning["crops"][0]
        self.assertEqual(result.status, "success")
        self.assertAlmostEqual(crop["yield_tonnes_per_acre"], 2.0)
        self.assertAlmostEqual(crop["yield_quintals_per_acre"], 20.0)
        self.assertAlmostEqual(crop["revenue_per_acre"], 60000.0)
        self.assertAlmostEqual(crop["profit_per_acre"], 50000.0)
        self.assertEqual(crop["allocated_acres"], 2.0)
        self.assertTrue(result.reasoning["constraints"]["land_limit_reached"])

    def test_optimizer_reasoning_discloses_the_nonoptimal_fallback(self):
        with (
            patch(
                "backend.optimizer.lp_solver.load_universal_economics",
                return_value={"rice": {"cost_per_acre": 10000}},
            ),
            patch(
                "backend.optimizer.lp_solver.linprog",
                return_value=SimpleNamespace(success=False),
            ),
        ):
            result = main.optimize_crop_allocation(
                land_acres=2,
                budget=100000,
                candidate_crops=["rice"],
                custom_yields={"rice": 4.942},
                custom_prices={"rice": 3000},
            )

        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.reasoning["objective"], "fallback_not_optimal")
        self.assertEqual(result.reasoning["crops"][0]["decision_note"], "fallback_assignment")

    def test_optimizer_reasoning_uses_configured_quintals_when_no_custom_yield(self):
        with patch(
            "backend.optimizer.lp_solver.load_universal_economics",
            return_value={
                "rice": {
                    "cost_per_acre": 1000,
                    "avg_yield_tonnes_per_hectare": 2.471,
                    "avg_yield_quintals_per_acre": 12,
                },
            },
        ):
            result = main.optimize_crop_allocation(
                land_acres=1,
                budget=10000,
                candidate_crops=["rice"],
                custom_prices={"rice": 100},
            )

        crop = result.reasoning["crops"][0]
        self.assertEqual(crop["yield_quintals_per_acre"], 12)
        self.assertEqual(crop["revenue_per_acre"], 1200)
        self.assertEqual(crop["profit_per_acre"], 200)

    def test_yield_estimator_prefers_district_and_falls_back_by_scope(self):
        lookup = {
            "rice": {
                "global_median": 1.1,
                "by_season": {"kharif": {"median": 1.2}},
                "by_state_season": {
                    "telangana|kharif": {"median": 2.3},
                },
                "by_district_season": {
                    "telangana|nizamabad|kharif": {"median": 3.4},
                },
            },
            "maize": {
                "global_median": 1.5,
                "by_state_season": {
                    "telangana|kharif": {"median": 2.5},
                },
                "by_district_season": {},
            },
            "cotton": {
                "global_median": 1.6,
                "by_district_season": {},
                "by_state_season": {},
                "by_season": {},
            },
        }
        with patch.object(main, "_yield_lookup", lookup):
            predictions = main._predict_yields(
                ["rice", "maize", "cotton", "coffee"],
                state="Telangana",
                district="Nizamabad",
                season="Kharif",
            )

        self.assertEqual(predictions, {
            "rice": 3.4,
            "maize": 2.5,
            "cotton": 1.6,
            "coffee": None,
        })

    def test_missing_soil_test_uses_mapped_or_mock_soil_inputs(self):
        request = main.OptimizeRequest(location="Nizamabad", budget=50000, land_acres=2)
        optimizer_result = SimpleNamespace(
            status="success",
            message="optimized",
            allocations=[],
            total_estimated_profit=1234.0,
            utilized_budget=10000.0,
            allocated_land_acres=1.0,
        )
        with (
            patch(
                "backend.main.urllib.request.urlopen",
                side_effect=[
                    MockResponse(self.geocoding),
                    MockResponse(self.weather),
                ],
            ),
            patch(
                "backend.main.get_soil_health_summary",
                return_value=self.soil_summary,
            ),
            patch(
                "backend.main.get_soil_inputs",
                return_value=(
                    {"nitrogen": 85, "phosphorus": 42, "potassium": 40, "ph": 6.8},
                    {
                        feature: {"status": "mock", "source": "Prototype static mock JSON"}
                        for feature in ("nitrogen", "phosphorus", "potassium", "ph")
                    },
                    {"mock_location_key": "nizamabad"},
                ),
            ) as soil_inputs,
            patch(
                "backend.main._predict_crop_recommendations",
                return_value=(["rice"], {"method": "test", "crops": []}),
            ),
            patch("backend.main._predict_yields", return_value={"rice": 4.2}),
            patch("backend.main._yield_lookup", {"rice": {}}),
            patch(
                "backend.main.get_crop_prices",
                return_value=({"rice": 3500.0}, {"rice": {"status": "live"}}),
            ) as price_lookup,
            patch("backend.main.optimize_crop_allocation", return_value=optimizer_result),
            patch(
                "backend.main._generate_ai_reasoning",
                return_value={"status": "not_configured", "error_code": "missing_api_key"},
            ) as ai_reasoning,
        ):
            result = main.optimize_farm(request)

        soil_inputs.assert_called_once()
        price_lookup.assert_called_once_with(
            ["rice"], state="Telangana", district="Nizamabad"
        )
        self.assertEqual(result["regional_data"]["nitrogen"], 85)
        self.assertTrue(result["soil_data_is_simulated"])
        self.assertTrue(result["plan_is_simulated"])
        self.assertEqual(result["profit_estimate_type"], "simulated")
        self.assertEqual(result["ai_reasoning"]["status"], "not_configured")
        ai_reasoning.assert_called_once()
        self.assertEqual(ai_reasoning.call_args.kwargs["farmer_inputs"]["land_acres"], 2)

    def test_optimization_receives_mandi_and_mock_prices_and_marks_plan_simulated(self):
        request = main.OptimizeRequest(
            location="Nizamabad",
            budget=100000,
            land_acres=4,
            soil_test={
                "nitrogen": 90,
                "phosphorus": 42,
                "potassium": 43,
                "ph": 6.5,
            },
        )
        optimizer_result = SimpleNamespace(
            status="success",
            message="optimized",
            allocations=[],
            total_estimated_profit=1234.0,
            utilized_budget=10000.0,
            allocated_land_acres=1.0,
        )
        with (
            patch(
                "backend.main.urllib.request.urlopen",
                side_effect=[
                    MockResponse(self.geocoding),
                    MockResponse(self.weather),
                ],
            ),
            patch(
                "backend.main.get_soil_health_summary",
                return_value=self.soil_summary,
            ),
            patch(
                "backend.main._predict_crop_recommendations",
                return_value=(
                    ["rice", "maize", "cotton"],
                    {"method": "test", "crops": []},
                ),
            ),
            patch(
                "backend.main._predict_yields",
                return_value={"rice": 4.2, "maize": 3.8, "cotton": 2.0},
            ),
            patch(
                "backend.main.get_crop_prices",
                return_value=(
                    {"rice": 3200.0, "maize": 2150.0},
                    {
                        "rice": {"status": "live", "source": "Mandi API"},
                        "maize": {
                            "status": "mock",
                            "source": "Prototype mock price",
                            "reason": "No Mandi quote.",
                        },
                    },
                ),
            ) as price_lookup,
            patch(
                "backend.main.optimize_crop_allocation",
                return_value=optimizer_result,
            ) as optimizer,
            patch(
                "backend.main._generate_ai_reasoning",
                return_value={"status": "not_configured", "error_code": "missing_api_key"},
            ),
        ):
            result = main.optimize_farm(request)

        price_lookup.assert_called_once_with(
            ["rice", "maize", "cotton"],
            state="Telangana",
            district="Nizamabad",
        )
        optimizer.assert_called_once()
        call = optimizer.call_args.kwargs
        self.assertEqual(call["candidate_crops"], ["rice", "maize"])
        self.assertEqual(call["custom_yields"], {"rice": 4.2, "maize": 3.8})
        self.assertEqual(call["custom_prices"], {"rice": 3200.0, "maize": 2150.0})
        self.assertEqual(result["top_crops_recommended"], ["rice", "maize", "cotton"])
        self.assertEqual(result["classifier_reasoning"]["method"], "test")
        self.assertEqual(result["optimized_crops"], ["rice", "maize"])
        self.assertEqual(result["regional_data"]["nitrogen"], 90)
        self.assertTrue(result["price_data_is_simulated"])
        self.assertEqual(result["profit_estimate_type"], "simulated")
        self.assertIn("SIMULATED PROTOTYPE PLAN", result["message"])


if __name__ == "__main__":
    unittest.main()
