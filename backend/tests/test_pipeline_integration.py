from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import requests as http_requests

from backend import data_sources, main
from scripts.benchmark import _mock_external_services, _responses_match, _start_server

GOLDEN_PATH = Path(__file__).resolve().parents[2] / "data" / "bench_golden.json"


@contextmanager
def local_api():
    server, thread, port = _start_server(main.app)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("The integration-test server did not shut down.")


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        main._request_data_cache.clear()
        data_sources._cache.clear()

    def test_all_benchmark_responses_match_the_unoptimized_goldens(self):
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        with (
            _mock_external_services(main, data_sources),
            patch.dict(os.environ, {"TIMING": "1"}),
            local_api() as base_url,
        ):
            for item in golden:
                prefetch_response = http_requests.post(
                    f"{base_url}/api/prefetch",
                    json={"location": item["request"]["location"]},
                    timeout=60,
                )
                self.assertEqual(prefetch_response.status_code, 200)
                self.assertEqual(prefetch_response.json()["status"], "warmed")
                response = http_requests.post(
                    f"{base_url}/api/optimize",
                    json=item["request"],
                    timeout=60,
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("classifier_and_shap", response.headers["server-timing"])
                self.assertTrue(
                    _responses_match(response.json(), item["response"]),
                    item["request"]["location"],
                )

    def test_twenty_concurrent_requests_have_no_cross_location_leakage(self):
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        expected = {
            item["request"]["location"]: item["response"]
            for item in golden
        }
        requests = [item["request"] for item in golden] * 4
        with (
            _mock_external_services(main, data_sources),
            local_api() as base_url,
            ThreadPoolExecutor(max_workers=20) as executor,
        ):
            responses = list(executor.map(
                lambda payload: http_requests.post(
                    f"{base_url}/api/optimize",
                    json=payload,
                    timeout=60,
                ),
                requests,
            ))

        for request, response in zip(requests, responses):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(
                _responses_match(
                    response.json(),
                    expected[request["location"]],
                ),
                request["location"],
            )
