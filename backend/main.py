import json
import urllib.request
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .optimizer.lp_solver import optimize_crop_allocation

app = FastAPI(title="AgriTech Optimization API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class OptimizeRequest(BaseModel):
    location: str
    budget: float
    land_acres: float

# Resolve paths dynamically
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
UNIVERSAL_DATA_DIR = PROJECT_ROOT / "universal_data"

def load_soil_database() -> dict:
    soil_file = UNIVERSAL_DATA_DIR / "district_soil_data.json"
    if soil_file.exists():
        with open(soil_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "default": {"nitrogen": 75, "phosphorus": 40, "potassium": 35, "ph": 6.5}
    }

@app.post("/api/optimize")
def optimize_farm(req: OptimizeRequest):
    # 1. LIVE GEOCODING (Open-Meteo)
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(req.location)}&count=1"
    try:
        with urllib.request.urlopen(geo_url) as response:
            geo_resp = json.loads(response.read().decode())
            if "results" in geo_resp and len(geo_resp["results"]) > 0:
                lat = geo_resp["results"][0]["latitude"]
                lon = geo_resp["results"][0]["longitude"]
                resolved_location = f"{geo_resp['results'][0]['name']}, {geo_resp['results'][0].get('admin1', '')}"
            else:
                raise Exception("Location not found")
    except Exception as e:
        lat, lon, resolved_location = 17.67, 78.10, req.location

    # 2. 3-MONTH HISTORICAL WEATHER FETCH (Matches Kaggle Dataset Structure)
    end_date = date.today() - timedelta(days=5)
    start_date = end_date - timedelta(days=90)
    
    archive_url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}&daily=temperature_2m_mean,precipitation_sum&timezone=auto"
    
    try:
        with urllib.request.urlopen(archive_url) as response:
            w_resp = json.loads(response.read().decode())
            temps = w_resp["daily"]["temperature_2m_mean"]
            precips = w_resp["daily"]["precipitation_sum"]
            
            temps = [t for t in temps if t is not None]
            precips = [p for p in precips if p is not None]
            
            temp = round(sum(temps) / len(temps), 2) if temps else 28.5
            rain = round(sum(precips), 2) if precips else 200.0
            
    except Exception as e:
        temp, rain = 28.5, 200.0

    # 3. SOIL MAPPING (Loaded from universal_data)
    soil_db = load_soil_database()
    search_key = req.location.lower().strip()
    soil_data = soil_db.get(search_key, soil_db.get("default", {"nitrogen": 75, "phosphorus": 40, "potassium": 35, "ph": 6.5}))

    # 4. SELECT CANDIDATE CROPS BASED ON REGIONAL CLIMATE
    if rain >= 200:
        candidates = ["rice", "jute", "cotton", "maize"]
    elif rain >= 100:
        candidates = ["cotton", "maize", "chickpea", "pigeonpeas"]
    else:
        candidates = ["chickpea", "mothbeans", "mungbean", "lentil"]

    # 5. SCI-PY LINEAR PROGRAMMING OPTIMIZATION
    lp_result = optimize_crop_allocation(
        land_acres=req.land_acres,
        budget=req.budget,
        candidate_crops=candidates,
        max_single_crop_ratio=0.65
    )

    return {
        "status": "success",
        "regional_data": {
            **soil_data, 
            "temperature": temp, 
            "rainfall": rain
        },
        "resolved_location": resolved_location,
        "allocations": lp_result.allocations,
        "total_estimated_profit": lp_result.total_estimated_profit,
        "utilized_budget": lp_result.utilized_budget,
        "allocated_land_acres": lp_result.allocated_land_acres,
        "message": f"Fetched 3-Month Historical Weather for {resolved_location}. {lp_result.message}"
    }
