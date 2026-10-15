import unittest
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from backend import data_sources


class DataSourceTests(unittest.TestCase):
    def setUp(self):
        data_sources._cache.clear()
        with data_sources._mandi_rate_limit_lock:
            data_sources._mandi_retry_after = 0

    def test_normalize_name_matches_location_variants(self):
        self.assertEqual(data_sources.normalize_name("Tamil Nadu"), "tamil nadu")
        self.assertEqual(data_sources.normalize_name("NIZAMABAD-DISTRICT"), "nizamabad district")
        self.assertEqual(data_sources.normalize_name("Guntūr District"), "guntur district")
        self.assertEqual(data_sources.normalize_name(None), "")
        self.assertEqual(data_sources.normalize_district("Nizamabad District"), "nizamabad")

    def test_soil_summary_aggregates_latest_district_cycle_only(self):
        def graphql(query, variables=None):
            if "getState" in query:
                return {"getState": [{"_id": "state-id", "name": "TELANGANA"}]}
            if "getdistrictAndSubdistrictBystate" in query:
                return {
                    "getdistrictAndSubdistrictBystate": [
                        {"_id": "district-id", "name": "NIZAMABAD"},
                    ]
                }
            return {
                "getNutrientDashboardForPortal": [
                    {
                        "cycle": "2025-26",
                        "results": {"n": {"Low": 5, "Medium": 2}, "pH": {"Neutral": 7}},
                    },
                    {
                        "cycle": "2025-26",
                        "results": {"n": {"Low": 3}, "pH": {"Neutral": 4}},
                    },
                    {"cycle": "2024-25", "results": {"n": {"High": 999}}},
                ]
            }

        with patch.object(data_sources, "_soil_graphql", side_effect=graphql):
            summary = data_sources.get_soil_health_summary(
                "Telangana", "Nizamabad District"
            )

        self.assertEqual(summary["cycle"], "2025-26")
        self.assertEqual(summary["category_counts"]["n"], {"Low": 8, "Medium": 2})
        self.assertEqual(summary["category_counts"]["pH"], {"Neutral": 11})
        self.assertIn("not exact numeric", summary["note"])

    def test_soilgrids_pH_uses_static_mock_for_incompatible_and_missing_features(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_path = Path(temp_dir) / "district_soil_data.json"
            mock_path.write_text(
                '{"guntur":{"nitrogen":90,"phosphorus":45,"potassium":48,"ph":7.2},'
                '"default":{"nitrogen":75,"phosphorus":40,"potassium":35,"ph":6.5}}',
                encoding="utf-8",
            )
            with (
                patch.object(data_sources, "MOCK_SOIL_PATH", mock_path),
                patch.object(
                    data_sources,
                    "_get_soilgrids_values",
                    return_value={"ph": 6.8, "nitrogen_g_per_kg": 1.2},
                ),
            ):
                values, provenance, metadata = data_sources.get_soil_inputs(
                    16.24, 80.64, ["Guntūr District"]
                )

        self.assertEqual(
            values,
            {"nitrogen": 90.0, "phosphorus": 45.0, "potassium": 48.0, "ph": 6.8},
        )
        self.assertEqual(provenance["ph"]["status"], "api_estimate")
        self.assertEqual(provenance["nitrogen"]["status"], "mock")
        self.assertEqual(provenance["phosphorus"]["status"], "mock")
        self.assertEqual(metadata["api_values"]["nitrogen_g_per_kg"], 1.2)
        self.assertEqual(metadata["mock_location_key"], "guntur")

    def test_soilgrids_empty_response_uses_location_mock_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_path = Path(temp_dir) / "district_soil_data.json"
            mock_path.write_text(
                '{"tenali":{"nitrogen":88,"phosphorus":44,"potassium":46,"ph":7.0},'
                '"default":{"nitrogen":75,"phosphorus":40,"potassium":35,"ph":6.5}}',
                encoding="utf-8",
            )
            with (
                patch.object(data_sources, "MOCK_SOIL_PATH", mock_path),
                patch.object(data_sources, "_get_soilgrids_values", return_value={}),
            ):
                values, provenance, metadata = data_sources.get_soil_inputs(
                    16.24, 80.64, ["Tenali"]
                )

        self.assertEqual(values["nitrogen"], 88.0)
        self.assertTrue(all(item["status"] == "mock" for item in provenance.values()))
        self.assertEqual(metadata["mock_location_key"], "tenali")

    def test_soilgrids_failure_uses_mock_and_surfaces_source_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_path = Path(temp_dir) / "district_soil_data.json"
            mock_path.write_text(
                '{"default":{"nitrogen":75,"phosphorus":40,"potassium":35,"ph":6.5}}',
                encoding="utf-8",
            )
            with (
                patch.object(data_sources, "MOCK_SOIL_PATH", mock_path),
                patch.object(
                    data_sources,
                    "_get_soilgrids_values",
                    side_effect=data_sources.DataSourceError("source timed out"),
                ),
            ):
                values, provenance, metadata = data_sources.get_soil_inputs(
                    16.24, 80.64, ["Unknown district"]
                )

        self.assertEqual(values["ph"], 6.5)
        self.assertEqual(metadata["api_error"], "source timed out")
        self.assertEqual(metadata["mock_location_key"], "default")
        self.assertTrue(all(item["status"] == "mock" for item in provenance.values()))

    def test_soilgrids_parser_scales_pH_and_reports_total_nitrogen_separately(self):
        response = Mock()
        response.json.return_value = {
            "properties": {
                "layers": [
                    {
                        "name": "phh2o",
                        "depths": [{"values": {"mean": 68}}],
                    },
                    {
                        "name": "nitrogen",
                        "unit_measure": {"d_factor": 100},
                        "depths": [{"values": {"mean": 125}}],
                    },
                ]
            }
        }
        with patch.object(data_sources.requests, "get", return_value=response):
            values = data_sources._get_soilgrids_values(16.24, 80.64)

        self.assertEqual(values, {"ph": 6.8, "nitrogen_g_per_kg": 1.25})

    def test_mandi_prices_aggregate_latest_district_reports(self):
        today = data_sources.date.today().isoformat()
        payload = {
            "data": [
                {
                    "state": "Maharashtra",
                    "district": "NIZAMABAD District",
                    "commodity": "Rice",
                    "market": "Market A",
                    "modal_price": 3000,
                    "arrival_date": today,
                },
                {
                    "state": "Maharashtra",
                    "district": "Nizamabad",
                    "commodity": "Rice",
                    "market": "Market B",
                    "modal_price": 3400,
                    "arrival_date": today,
                },
                {
                    "state": "Maharashtra",
                    "district": "Nizamabad",
                    "commodity": "Rice",
                    "market": "Old market record",
                    "modal_price": 9000,
                    "arrival_date": "2020-01-01",
                },
                {
                    "state": "Maharashtra",
                    "district": "Other district",
                    "commodity": "Rice",
                    "market": "Other market",
                    "modal_price": 7000,
                    "arrival_date": today,
                },
            ]
        }

        with (
            patch.object(
                data_sources, "TGK_ELIGIBLE_CROPS", frozenset({"rice"})
            ),
            patch.object(
                data_sources,
                "_load_price_mappings",
                return_value=(
                    {"rice": "Rice"},
                    {"rice": 3500},
                    ["Maharashtra"],
                    "https://example.test",
                ),
            ),
            patch.object(data_sources, "_get_mandi_result", return_value=payload),
        ):
            prices, details = data_sources.get_crop_prices(
                ["rice"], state="Maharashtra", district="Nizamabad"
            )

        self.assertEqual(prices, {"rice": 3200.0})
        self.assertEqual(details["rice"]["status"], "live")
        self.assertEqual(details["rice"]["source"], "tgk_live")
        self.assertEqual(details["rice"]["scope"], "district_aggregate")
        self.assertEqual(details["rice"]["markets"], ["Market A", "Market B"])
        self.assertFalse(details["rice"]["is_stale"])

    def test_mandi_prices_fall_back_to_state_average(self):
        today = data_sources.date.today().isoformat()

        def mandi_result(_base_url, endpoint, _params):
            if endpoint == "prices":
                return {
                    "data": [
                        {
                            "state": "Maharashtra",
                            "district": "Other district",
                            "commodity": "Rice",
                            "market": "Other market",
                            "modal_price": 7000,
                            "arrival_date": today,
                        }
                    ]
                }
            return {
                "data": [
                    {
                        "arrival_date": today,
                        "avg_modal_price": 3250,
                        "data_points": 4,
                    }
                ]
            }

        with (
            patch.object(
                data_sources, "TGK_ELIGIBLE_CROPS", frozenset({"rice"})
            ),
            patch.object(
                data_sources,
                "_load_price_mappings",
                return_value=(
                    {"rice": "Rice"},
                    {"rice": 3500},
                    ["Maharashtra"],
                    "https://example.test",
                ),
            ),
            patch.object(data_sources, "_get_mandi_result", side_effect=mandi_result),
        ):
            prices, details = data_sources.get_crop_prices(
                ["rice"], state="Maharashtra", district="Nizamabad"
            )

        self.assertEqual(prices, {"rice": 3250.0})
        self.assertEqual(details["rice"]["scope"], "state_average")
        self.assertEqual(details["rice"]["data_points"], 4)

    def test_mandi_outage_uses_mock_prices_and_surfaces_error(self):
        with (
            patch.object(
                data_sources, "TGK_ELIGIBLE_CROPS", frozenset({"rice"})
            ),
            patch.object(
                data_sources,
                "_load_price_mappings",
                return_value=(
                    {"rice": "Rice"},
                    {"rice": 3500},
                    ["Maharashtra"],
                    "https://example.test",
                ),
            ),
            patch.object(
                data_sources,
                "_get_mandi_result",
                side_effect=data_sources.DataSourceError("Mandi API unavailable"),
            ),
            patch.object(data_sources, "_load_ceda_crop_map", return_value={}),
            patch.object(data_sources, "_load_ceda_price_cache", return_value={}),
        ):
            prices, details = data_sources.get_crop_prices(
                ["rice"], state="Maharashtra", district="Nizamabad"
            )

        self.assertEqual(prices, {"rice": 3500.0})
        self.assertEqual(details["rice"]["status"], "mock")
        self.assertIn("Mandi API unavailable", details["rice"]["reason"])

    def test_mandi_rate_limit_is_reported_and_throttled(self):
        response = Mock()
        response.status_code = 429
        rate_limit_error = data_sources.requests.HTTPError(
            "Too Many Requests",
            response=response,
        )
        with patch.object(
            data_sources.requests,
            "get",
            side_effect=rate_limit_error,
        ) as request:
            with self.assertRaises(data_sources.DataSourceError) as first_error:
                data_sources._get_mandi_json(
                    "https://example.test/v1/prices",
                    {"state": "Maharashtra", "commodity": "Rice"},
                )
            with self.assertRaises(data_sources.DataSourceError) as retry_error:
                data_sources._get_mandi_json(
                    "https://example.test/v1/prices",
                    {"state": "Maharashtra", "commodity": "Maize"},
                )

        self.assertEqual(first_error.exception.status_code, 429)
        self.assertEqual(retry_error.exception.status_code, 429)
        request.assert_called_once()

    def test_unsupported_state_uses_mock_without_calling_api(self):
        with (
            patch.object(
                data_sources,
                "_load_price_mappings",
                return_value=(
                    {"rice": "Rice"},
                    {"rice": 3500},
                    ["Maharashtra", "Punjab"],
                    "https://example.test",
                ),
            ),
            patch.object(data_sources, "_get_mandi_result") as mandi_request,
            patch.object(data_sources, "_load_ceda_crop_map", return_value={}),
            patch.object(data_sources, "_load_ceda_price_cache", return_value={}),
        ):
            prices, details = data_sources.get_crop_prices(
                ["rice"], state="Andhra Pradesh", district="Guntur"
            )

        self.assertEqual(prices, {"rice": 3500.0})
        self.assertEqual(details["rice"]["status"], "mock")
        self.assertIn("does not cover Andhra Pradesh", details["rice"]["reason"])
        mandi_request.assert_not_called()

    def test_old_mandi_reports_are_marked_stale(self):
        old_date = (data_sources.date.today() - timedelta(days=30)).isoformat()
        result = data_sources._district_price(
            [
                {
                    "state": "Maharashtra",
                    "district": "Nizamabad",
                    "commodity": "Rice",
                    "market": "Market A",
                    "modal_price": 3200,
                    "arrival_date": old_date,
                }
            ],
            "Maharashtra",
            "Nizamabad",
            "Rice",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result[1]["is_stale"])


if __name__ == "__main__":
    unittest.main()
