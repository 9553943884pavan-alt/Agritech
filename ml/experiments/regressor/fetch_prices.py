"""Display district/state Mandi API prices and labelled prototype mock prices."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_sources import get_crop_prices


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="Maharashtra")
    parser.add_argument("--district", default="")
    args = parser.parse_args()

    prices_path = PROJECT_ROOT / "universal_data" / "crop_prices.json"
    with prices_path.open("r", encoding="utf-8") as prices_file:
        crops = [crop for crop in json.load(prices_file) if crop != "default"]

    prices, details = get_crop_prices(
        crops,
        state=args.state,
        district=args.district,
    )
    simulated_market_data = False
    print(f"Mandi API and prototype prices for {args.district or args.state} (Rs. per quintal)")
    print("-" * 88)
    for crop in crops:
        detail = details[crop]
        price = prices.get(crop)
        if detail["status"] == "live":
            scope = detail["scope"].replace("_", " ")
            label = f"LIVE MANDI API ({scope}, reported {detail['arrival_date']})"
            if detail["is_stale"]:
                label += " - STALE"
                simulated_market_data = True
        elif detail["status"] == "mock":
            label = "MOCK ONLY - NOT A REAL QUOTE"
            simulated_market_data = True
        else:
            label = f"UNAVAILABLE: {detail['reason']}"
        price_text = f"{price:,.2f}" if price is not None else "N/A"
        print(f"{crop:16} Rs. {price_text:>10} | {label}")
        if detail["status"] == "mock":
            print(f"  Reason: {detail['reason']}")

    if simulated_market_data:
        print("\nCAUTION: mock or stale prices must not be treated as current market quotes.")


if __name__ == "__main__":
    main()
