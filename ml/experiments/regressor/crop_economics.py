"""Estimate per-acre crop revenue using Mandi API or prototype mock prices."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_sources import get_crop_prices


def get_price_per_quintal(
    crop_name: str,
    state: str | None = None,
    district: str | None = None,
) -> float:
    crop = crop_name.lower().strip()
    prices, _ = get_crop_prices([crop], state=state, district=district)
    return prices.get(crop, 0.0)


def calculate_revenue_per_acre(
    crop_name: str,
    predicted_yield_tonnes_per_hectare: float,
    state: str | None = None,
    district: str | None = None,
) -> float:
    yield_tonnes_per_acre = predicted_yield_tonnes_per_hectare / 2.471
    yield_quintals_per_acre = yield_tonnes_per_acre * 10
    price_per_quintal = get_price_per_quintal(
        crop_name,
        state=state,
        district=district,
    )
    return yield_quintals_per_acre * price_per_quintal


if __name__ == "__main__":
    revenue = calculate_revenue_per_acre(
        "cotton",
        2.0,
        state="Maharashtra",
        district="Buldhana",
    )
    print(f"Estimated revenue for 1 acre of cotton: Rs. {revenue:,.2f}")
