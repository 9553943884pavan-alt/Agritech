import requests

# ---------------------------------------------------------
# STATIC FALLBACK DICTIONARY (Average Wholesale Price in ₹ per Quintal)
# Includes all 22 crops. Used as a fallback if the API fails, 
# or for crops the API doesn't support (like fruits).
# ---------------------------------------------------------
CROP_PRICES_PER_QUINTAL = {
    # The 8 crops covered by API (Fallback prices just in case)
    "chickpea": 7000,
    "pigeonpeas": 8500,
    "mungbean": 7800,
    "blackgram": 7800,
    "lentil": 6200,
    "cotton": 8500,
    "rice": 3500,
    "coconut": 18000,
    
    # The 14 crops we researched from the internet
    "apple": 6500,
    "banana": 1800,
    "coffee": 30000,
    "grapes": 5000,
    "jute": 5050,           # Minimum Support Price (MSP) roughly
    "kidneybeans": 9000,    # Rajma
    "maize": 2200,          # MSP roughly
    "mango": 4000,
    "mothbeans": 7000,
    "muskmelon": 1500,
    "orange": 3000,
    "papaya": 1500,
    "pomegranate": 7000,
    "watermelon": 1000
}

# Mapping our 8 crops to the exact TGK Agro API names
API_NAME_MAPPING = {
    "chickpea": "Desi Chana",
    "pigeonpeas": "Toor (Arhar)",
    "mungbean": "Moong",
    "blackgram": "Urad",
    "lentil": "Masoor",
    "cotton": "Raw Cotton (Kapas)",
    "rice": "Rice",
    "coconut": "Copra"
}

def get_price_per_quintal(crop_name):
    """
    Tries to get the live price from API. If it fails or the crop isn't 
    supported by the API, it falls back to the hardcoded dictionary.
    """
    crop_name = crop_name.lower().strip()
    
    # 1. If it's one of our 8 API crops, try the live API first
    if crop_name in API_NAME_MAPPING:
        api_target_name = API_NAME_MAPPING[crop_name]
        url = "https://tgkagro.com/prices/live_prices.json"
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                for key, item in data.items():
                    if item.get('name') == api_target_name:
                        return float(item.get('inr_per_quintal'))
        except Exception as e:
            print(f"API failed for {crop_name}. Using fallback.")
            pass # If API fails, just fall through to the dictionary
            
    # 2. Return the dictionary fallback price
    return CROP_PRICES_PER_QUINTAL.get(crop_name, 0)

def calculate_revenue_per_acre(crop_name, predicted_yield_tonnes_per_hectare):
    """
    Does all the unit conversion math:
    - Converts Hectares to Acres
    - Converts Tonnes to Quintals
    """
    # Convert Yield from Hectares to Acres (1 Hectare = 2.471 Acres)
    yield_tonnes_per_acre = predicted_yield_tonnes_per_hectare / 2.471
    
    # Convert Tonnes to Quintals (1 Tonne = 10 Quintals)
    yield_quintals_per_acre = yield_tonnes_per_acre * 10
    
    # Get the price (Live API or Fallback)
    price_per_quintal = get_price_per_quintal(crop_name)
    
    # Calculate Final Revenue for 1 Acre
    revenue_per_acre = yield_quintals_per_acre * price_per_quintal
    
    return revenue_per_acre

# --- QUICK TEST ---
if __name__ == "__main__":
    print("Testing Live API Crop (Cotton):")
    # Let's pretend the AI predicts Cotton will yield 2 Tonnes per Hectare
    rev_cotton = calculate_revenue_per_acre("cotton", 2.0)
    print(f"Revenue for 1 Acre of Cotton: Rs. {rev_cotton:,.2f}\n")
    
    print("Testing Internet Hardcoded Crop (Apple):")
    # Let's pretend the AI predicts Apples will yield 15 Tonnes per Hectare
    rev_apple = calculate_revenue_per_acre("apple", 15.0)
    print(f"Revenue for 1 Acre of Apple: Rs. {rev_apple:,.2f}")
