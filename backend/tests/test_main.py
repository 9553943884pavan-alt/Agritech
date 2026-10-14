import json
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
            patch("backend.main._predict_top_crops", return_value=["rice"]),
            patch("backend.main._predict_yields", return_value={"rice": 4.2}),
            patch("backend.main._yield_lookup", {"rice": {}}),
            patch(
                "backend.main.get_crop_prices",
                return_value=({"rice": 3500.0}, {"rice": {"status": "live"}}),
            ) as price_lookup,
            patch("backend.main.optimize_crop_allocation", return_value=optimizer_result),
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
                "backend.main._predict_top_crops",
                return_value=["rice", "maize", "cotton"],
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
        self.assertEqual(result["optimized_crops"], ["rice", "maize"])
        self.assertEqual(result["regional_data"]["nitrogen"], 90)
        self.assertTrue(result["price_data_is_simulated"])
        self.assertEqual(result["profit_estimate_type"], "simulated")
        self.assertIn("SIMULATED PROTOTYPE PLAN", result["message"])


if __name__ == "__main__":
    unittest.main()
