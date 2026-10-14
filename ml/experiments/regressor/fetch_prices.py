import requests
import json

def fetch_live_prices():
    url = "https://tgkagro.com/prices/live_prices.json"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        print(f"Fetching live prices from {url}...")
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print("Successfully fetched live data!\n")
            print("-" * 50)
            print("LIVE INDIAN CROP PRICES (Sample)")
            print("-" * 50)
            
            # The API returns a dictionary, so we iterate through its keys/values
            # Let's print the first 5 crops we find
            count = 0
            for key, item in data.items():
                if count >= 5:
                    break
                
                name = item.get('name', key.title())
                price_inr = item.get('inr_per_quintal', 'N/A')
                trend = item.get('pct_change', 0)
                
                trend_str = f"+{trend}%" if float(trend) > 0 else f"{trend}%"
                
                print(f"Crop: {name.ljust(20)} | Price: Rs {price_inr} / Quintal | Trend: {trend_str}")
                count += 1
                
            print("-" * 50)
        else:
            print(f"Failed to fetch data. Status Code: {response.status_code}")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    fetch_live_prices()
