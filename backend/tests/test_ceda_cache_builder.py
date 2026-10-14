import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import build_ceda_cache


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_real_wrapped_rows_computes_metrics_and_dropped_values():
    payload = json.loads(
        (FIXTURES / "ceda_prices_sample.json").read_text(encoding="utf-8")
    )

    metrics = build_ceda_cache.parse_price_rows(payload, crop="maize")

    assert metrics["median_modal"] == 3000
    assert metrics["mean_modal"] == 3000
    assert metrics["min_modal"] == 2000
    assert metrics["max_modal"] == 4000
    assert metrics["n_rows"] == 2
    assert metrics["dropped_null_or_zero"] == 2
    assert metrics["observed_from"] == "2025-10-27"
    assert metrics["observed_to"] == "2025-10-30"
    assert metrics["latest_date"] == "2025-10-30"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"data": []}, "output.data"),
        ({"output": {"data": {"date": "2025-10-30"}}}, "must be a list"),
        ({"output": {"data": [{"modal_price": 1}]}}, "date/modal_price"),
        (
            {"output": {"data": [{"date": "not-a-date", "modal_price": 1}]}},
            "YYYY-MM-DD",
        ),
    ],
)
def test_parse_fails_loudly_for_invalid_or_missing_fields(payload, message):
    with pytest.raises(build_ceda_cache.CacheBuildError, match=message):
        build_ceda_cache.parse_price_rows(payload, crop="maize")


def test_final_map_has_21_ids_and_kidneybeans_is_unmapped():
    crop_map = build_ceda_cache.load_crop_map()

    assert len(crop_map) == 22
    assert sum(item["commodity_id"] is not None for item in crop_map.values()) == 21
    assert crop_map["kidneybeans"]["commodity_id"] is None
    assert crop_map["mothbeans"]["proxy"] is True
    assert crop_map["rice"]["commodity_id"] == build_ceda_cache.RICE_COMMODITY_ID == 2


def test_sanity_warnings_are_nonfatal():
    warnings = build_ceda_cache._warn_sanity(
        "maize",
        {
            "median_modal": 1,
            "min_modal": 10,
            "max_modal": 5,
        },
    )

    assert len(warnings) == 2


def test_price_request_uses_bearer_auth_without_exposing_the_token(monkeypatch):
    request_headers = {}
    response = Mock(status_code=200, headers={})
    session = Mock()
    session.post.side_effect = lambda *_args, **kwargs: (
        request_headers.update(kwargs["headers"]) or response
    )
    monkeypatch.setattr(build_ceda_cache, "_save_json", lambda *_args: None)

    returned_response, rate_headers = build_ceda_cache._api_post(
        session,
        "test-token",
        4,
        progress={"call_timestamps": []},
    )

    assert returned_response is response
    assert rate_headers == {}
    assert request_headers["Authorization"] == "Bearer test-token"


def test_dry_run_prints_plan_without_reading_key_or_network(monkeypatch, capsys):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("dry run must not contact the API")

    monkeypatch.setattr(build_ceda_cache.requests.Session, "post", fail_if_called)
    result = build_ceda_cache.build(
        Namespace(
            only="maize,rice",
            max_calls=1,
            dry_run=True,
            force=False,
        )
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "DRY RUN: 1 planned CEDA calls" in output
    assert "maize: commodity_id=4" in output
