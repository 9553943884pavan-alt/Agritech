from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import data_sources


FIXTURE_CACHE = Path(__file__).parent / "fixtures" / "ceda_price_cache.csv"


@pytest.fixture
def configured_prices(monkeypatch):
    monkeypatch.setattr(data_sources, "CEDA_PRICE_CACHE_PATH", FIXTURE_CACHE)
    monkeypatch.setattr(data_sources, "_ceda_price_cache_loaded", False)
    monkeypatch.setattr(data_sources, "_ceda_price_cache", {})
    monkeypatch.setattr(
        data_sources,
        "_load_price_mappings",
        lambda: (
            {"rice": "Rice", "maize": "Maize", "mothbeans": "Moth"},
            {"rice": 3500, "maize": 2200, "mothbeans": 7000, "grapes": 5000,
             "apple": 6000, "kidneybeans": 9000},
            ["Maharashtra"],
            "https://example.test",
        ),
    )


def _today_quote(price=4100):
    return {
        "data": [
            {
                "state": "Maharashtra",
                "district": "Nashik",
                "commodity": "Rice",
                "market": "Test market",
                "modal_price": price,
                "arrival_date": date.today().isoformat(),
            }
        ]
    }


def test_fresh_eligible_tgk_quote_has_compatible_source_fields(
    configured_prices, monkeypatch
):
    monkeypatch.setattr(data_sources, "TGK_ELIGIBLE_CROPS", frozenset({"rice"}))
    with patch.object(
        data_sources, "_get_mandi_result", return_value=_today_quote()
    ) as request:
        prices, details = data_sources.get_crop_prices(
            ["rice"], state="Maharashtra", district="Nashik"
        )

    assert prices["rice"] == 4100
    assert details["rice"]["source"] == "tgk_live"
    assert details["rice"]["as_of"] == date.today().isoformat()
    assert details["rice"]["price_rs_per_quintal"] == 4100
    assert details["rice"]["is_simulated"] is False
    request.assert_called_once()


def test_noneligible_crop_uses_ceda_without_calling_tgk(configured_prices):
    with patch.object(data_sources, "_get_mandi_result") as request:
        prices, details = data_sources.get_crop_prices(["maize"])

    assert prices["maize"] == 4200
    assert details["maize"]["source"] == "ceda_cached"
    assert details["maize"]["as_of"] == "2025-10-30"
    assert details["maize"]["label"] == (
        "Historical mandi price, Oct 2025 (Agmarknet via CEDA)"
    )
    assert details["maize"]["is_simulated"] is False
    request.assert_not_called()


def test_stale_tgk_quote_falls_back_to_ceda(configured_prices, monkeypatch):
    monkeypatch.setattr(data_sources, "TGK_ELIGIBLE_CROPS", frozenset({"rice"}))
    stale_quote = {
        "data": [
            {
                "state": "Maharashtra",
                "district": "Nashik",
                "commodity": "Rice",
                "market": "Test market",
                "modal_price": 9000,
                "arrival_date": (date.today() - timedelta(days=30)).isoformat(),
            }
        ]
    }
    with patch.object(
        data_sources,
        "_get_mandi_result",
        side_effect=[stale_quote, {"data": []}],
    ):
        prices, details = data_sources.get_crop_prices(
            ["rice"], state="Maharashtra", district="Nashik"
        )

    assert prices["rice"] == 2900
    assert details["rice"]["source"] == "ceda_cached"


@pytest.mark.parametrize("crop", ["grapes", "apple"])
def test_sparse_or_incomplete_ceda_rows_fall_back_to_mock(
    configured_prices, crop
):
    prices, details = data_sources.get_crop_prices([crop])

    assert details[crop]["source"] == "mock"
    assert details[crop]["is_simulated"] is True
    assert details[crop]["price_rs_per_quintal"] == (
        5000 if crop == "grapes" else 6000
    )
    assert prices[crop] == details[crop]["price_per_quintal"]


def test_proxy_cached_price_is_marked_simulated_and_explained(configured_prices):
    prices, details = data_sources.get_crop_prices(["mothbeans"])

    assert prices["mothbeans"] == 4800
    assert details["mothbeans"]["source"] == "ceda_cached"
    assert details["mothbeans"]["proxy"] is True
    assert details["mothbeans"]["is_simulated"] is True
    assert details["mothbeans"]["warning"] == "Moth bean dal price used as a proxy."


def test_kidneybeans_always_uses_mock(configured_prices):
    with patch.object(data_sources, "_get_mandi_result") as request:
        prices, details = data_sources.get_crop_prices(
            ["kidneybeans"], state="Maharashtra"
        )

    assert prices["kidneybeans"] == 9000
    assert details["kidneybeans"]["source"] == "mock"
    assert details["kidneybeans"]["is_simulated"] is True
    assert "No verified CEDA commodity mapping" in details["kidneybeans"]["reason"]
    request.assert_not_called()


def test_missing_cache_file_falls_back_to_mock_without_crashing(
    configured_prices, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        data_sources, "CEDA_PRICE_CACHE_PATH", tmp_path / "missing-cache.csv"
    )
    monkeypatch.setattr(data_sources, "_ceda_price_cache_loaded", False)

    prices, details = data_sources.get_crop_prices(["maize"])

    assert prices["maize"] == 2200
    assert details["maize"]["source"] == "mock"
