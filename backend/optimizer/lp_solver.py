import json
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict, field
import numpy as np
from scipy.optimize import linprog
from ..timing import timed_call

# Resolve paths dynamically
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
UNIVERSAL_DATA_DIR = PROJECT_ROOT / "universal_data"

@lru_cache(maxsize=1)
def load_universal_economics() -> Dict[str, Any]:
    """Loads default crop economics and baseline costs from universal_data."""
    econ_file = UNIVERSAL_DATA_DIR / "crop_economics.json"
    if econ_file.exists():
        with open(econ_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

@dataclass
class CropAllocation:
    crop: str
    allocated_acres: float
    percentage_of_land: float
    cost_invested: float
    expected_yield_tonnes: float
    expected_revenue: float
    profit_est: float

@dataclass
class CropOptimizationResult:
    status: str
    message: str
    total_land_acres: float
    allocated_land_acres: float
    total_budget: float
    utilized_budget: float
    remaining_budget: float
    total_estimated_revenue: float
    total_estimated_profit: float
    allocations: List[Dict[str, Any]]
    reasoning: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def optimize_crop_allocation(
    land_acres: float,
    budget: float,
    candidate_crops: List[str],
    max_single_crop_ratio: float = 0.65,
    custom_yields: Optional[Dict[str, float]] = None,
    custom_prices: Optional[Dict[str, float]] = None,
    custom_costs: Optional[Dict[str, float]] = None
) -> CropOptimizationResult:
    """
    Solves the Agricultural Resource Allocation Problem using Linear Programming (SciPy linprog).
    
    Objective:
      Maximize Total Net Profit = Sum( (Revenue_per_acre_i - Cost_per_acre_i) * x_i )
      (Formulated in SciPy as minimizing -Profit)
      
    Subject to:
      1. Land Constraint:   Sum( x_i ) <= land_acres
      2. Budget Constraint: Sum( Cost_per_acre_i * x_i ) <= budget
      3. Risk / Diversification Constraint: x_i <= max_single_crop_ratio * land_acres
      4. Non-negativity:    x_i >= 0
    """
    if land_acres <= 0 or budget <= 0:
        return CropOptimizationResult(
            status="error",
            message="Land and budget must be strictly positive numbers.",
            total_land_acres=land_acres,
            allocated_land_acres=0.0,
            total_budget=budget,
            utilized_budget=0.0,
            remaining_budget=budget,
            total_estimated_revenue=0.0,
            total_estimated_profit=0.0,
            allocations=[]
        )

    all_economics = timed_call("economics_load", load_universal_economics)
    
    # Clean and filter candidate crops
    clean_candidates = [c.lower().strip() for c in candidate_crops]
    valid_crops = [c for c in clean_candidates if c in all_economics]
    
    # Fallback to defaults if candidate crops are empty or unrecognized
    if not valid_crops:
        valid_crops = ["rice", "maize", "chickpea"]

    n = len(valid_crops)
    
    # Vector parameters
    costs_per_acre = []
    revenues_per_acre = []
    yields_tonnes_per_acre = []
    yields_tonnes_per_hectare = []
    yields_quintals_per_acre = []
    prices_per_quintal = []
    
    for crop in valid_crops:
        econ = all_economics.get(crop, {})
        
        # Determine cost
        cost = (custom_costs or {}).get(crop, econ.get("cost_per_acre", 15000.0))
        
        # Determine price (per quintal)
        price = (custom_prices or {}).get(crop, econ.get("price_per_quintal", 3000.0))
        
        # Determine yield
        # 1 Hectare = 2.471 Acres; 1 Tonne = 10 Quintals
        if custom_yields and crop in custom_yields:
            yield_tonnes_ha = custom_yields[crop]
            yield_tonnes_acre = yield_tonnes_ha / 2.471
            yield_quintals_acre = yield_tonnes_acre * 10.0
        else:
            yield_tonnes_ha = econ.get("avg_yield_tonnes_per_hectare", 2.5)
            yield_tonnes_acre = yield_tonnes_ha / 2.471
            yield_quintals_acre = econ.get("avg_yield_quintals_per_acre", yield_tonnes_acre * 10.0)

        rev = yield_quintals_acre * price
        
        costs_per_acre.append(cost)
        revenues_per_acre.append(rev)
        yields_tonnes_per_acre.append(yield_tonnes_acre)
        yields_tonnes_per_hectare.append(yield_tonnes_ha)
        yields_quintals_per_acre.append(yield_quintals_acre)
        prices_per_quintal.append(price)
        
    costs_per_acre = np.array(costs_per_acre)
    revenues_per_acre = np.array(revenues_per_acre)
    yields_tonnes_per_acre = np.array(yields_tonnes_per_acre)
    
    # Objective Coefficients (SciPy minimizes c^T * x, so we minimize -Profit)
    profit_per_acre = revenues_per_acre - costs_per_acre
    c = -profit_per_acre

    # Inequality Constraints: A_ub * x <= b_ub
    # Row 1: Sum(x_i) <= land_acres
    # Row 2: Sum(cost_i * x_i) <= budget
    A_ub = np.array([
        [1.0] * n,
        costs_per_acre.tolist()
    ])
    b_ub = np.array([
        land_acres,
        budget
    ])

    # Bounds: 0 <= x_i <= max_cap
    # If multiple crops, cap single crop to max_single_crop_ratio * land_acres to encourage diversification
    single_crop_cap = land_acres if n == 1 else (land_acres * max_single_crop_ratio)
    bounds = [(0, single_crop_cap) for _ in range(n)]

    # Solve via Highs interior point / simplex solver
    res = timed_call(
        "scipy_linprog",
        linprog,
        c,
        A_ub=A_ub,
        b_ub=b_ub,
        bounds=bounds,
        method="highs",
    )

    allocations_list = []
    max_crop_acres = single_crop_cap

    def build_reasoning(allocated_acres: np.ndarray, method: str) -> Dict[str, Any]:
        allocated_acres = np.asarray(allocated_acres, dtype=float)
        used_land = float(np.sum(allocated_acres))
        used_budget = float(np.sum(allocated_acres * costs_per_acre))
        crop_rows = []
        for i, crop in enumerate(valid_crops):
            acres = float(allocated_acres[i])
            crop_cost = float(costs_per_acre[i])
            crop_revenue = float(revenues_per_acre[i])
            crop_profit = crop_revenue - crop_cost
            if not res.success:
                decision_note = "fallback_assignment"
            elif acres <= 0.001 and crop_profit <= 0:
                decision_note = "nonpositive_margin"
            elif acres <= 0.001:
                decision_note = "not_selected_in_mix"
            elif acres >= max_crop_acres - 0.01 and n > 1:
                decision_note = "crop_limit"
            else:
                decision_note = "positive_profit_mix"
            crop_rows.append({
                "crop": crop.title(),
                "yield_tonnes_per_hectare": float(yields_tonnes_per_hectare[i]),
                "yield_tonnes_per_acre": float(yields_tonnes_per_acre[i]),
                "yield_quintals_per_acre": float(yields_quintals_per_acre[i]),
                "price_per_quintal": float(prices_per_quintal[i]),
                "cost_per_acre": crop_cost,
                "revenue_per_acre": crop_revenue,
                "profit_per_acre": crop_profit,
                "allocated_acres": acres,
                "cost_invested": acres * crop_cost,
                "expected_revenue": acres * crop_revenue,
                "profit_contribution": acres * crop_profit,
                "max_crop_acres": max_crop_acres,
                "crop_cap_reached": acres >= max_crop_acres - 0.01,
                "decision_note": decision_note,
            })

        return {
            "method": method,
            "objective": "maximize_net_profit" if res.success else "fallback_not_optimal",
            "scope_note": "eligible_recommendations_only",
            "constraints": {
                "land_acres_available": float(land_acres),
                "land_acres_used": used_land,
                "land_acres_remaining": max(0.0, float(land_acres) - used_land),
                "land_limit_reached": used_land >= land_acres - 0.01,
                "budget_available": float(budget),
                "budget_used": used_budget,
                "budget_remaining": max(0.0, float(budget) - used_budget),
                "budget_limit_reached": used_budget >= budget - 0.01,
                "max_acres_per_crop": float(max_crop_acres),
                "crop_share_limit": (
                    float(max_single_crop_ratio) if n > 1 else 1.0
                ),
            },
            "crops": crop_rows,
        }
    
    if res.success:
        alloc_acres = np.round(res.x, 2)
        
        # Ensure sum does not slightly exceed due to floating point
        if np.sum(alloc_acres) > land_acres:
            alloc_acres = np.round(alloc_acres * (land_acres / np.sum(alloc_acres)), 2)
            
        total_allocated = float(np.sum(alloc_acres))
        total_cost = float(np.sum(alloc_acres * costs_per_acre))
        total_revenue = float(np.sum(alloc_acres * revenues_per_acre))
        total_profit = float(total_revenue - total_cost)

        for i, crop in enumerate(valid_crops):
            acres = float(alloc_acres[i])
            if acres > 0.001 or n <= 3:
                pct = float(round((acres / land_acres) * 100, 1)) if land_acres > 0 else 0.0
                c_invested = float(round(acres * costs_per_acre[i], 2))
                y_tonnes = float(round(acres * yields_tonnes_per_acre[i], 2))
                c_rev = float(round(acres * revenues_per_acre[i], 2))
                c_profit = float(round(c_rev - c_invested, 2))
                
                allocations_list.append(asdict(CropAllocation(
                    crop=crop.title(),
                    allocated_acres=float(acres),
                    percentage_of_land=pct,
                    cost_invested=c_invested,
                    expected_yield_tonnes=y_tonnes,
                    expected_revenue=c_rev,
                    profit_est=c_profit
                )))
                
        # Sort by allocated acres descending
        allocations_list.sort(key=lambda x: x["allocated_acres"], reverse=True)
        reasoning = build_reasoning(alloc_acres, "linear_program_highs")

        return CropOptimizationResult(
            status="success",
            message="Optimized mathematically using Linear Programming (SciPy HiGHS solver).",
            total_land_acres=land_acres,
            allocated_land_acres=round(total_allocated, 2),
            total_budget=budget,
            utilized_budget=round(total_cost, 2),
            remaining_budget=round(max(0.0, budget - total_cost), 2),
            total_estimated_revenue=round(total_revenue, 2),
            total_estimated_profit=round(total_profit, 2),
            allocations=allocations_list,
            reasoning=reasoning,
        )
    else:
        # Fallback to safe proportional split if budget is extremely tight
        split_acres = round(land_acres / n, 2)
        total_cost = float(np.sum([split_acres * costs_per_acre[i] for i in range(n)]))
        scale = min(1.0, budget / total_cost) if total_cost > 0 else 1.0
        
        fallback_allocs = []
        for i, crop in enumerate(valid_crops):
            acres = round(split_acres * scale, 2)
            c_cost = round(acres * costs_per_acre[i], 2)
            y_tonnes = round(acres * yields_tonnes_per_acre[i], 2)
            c_rev = round(acres * revenues_per_acre[i], 2)
            c_profit = round(c_rev - c_cost, 2)
            fallback_allocs.append({
                "crop": crop.title(),
                "allocated_acres": acres,
                "percentage_of_land": round((acres / land_acres) * 100, 1),
                "cost_invested": c_cost,
                "expected_yield_tonnes": y_tonnes,
                "expected_revenue": c_rev,
                "profit_est": c_profit
            })
        reasoning = build_reasoning(
            np.array([allocation["allocated_acres"] for allocation in fallback_allocs]),
            "proportional_fallback",
        )
            
        return CropOptimizationResult(
            status="fallback",
            message="Linear program bound constrained. Applied safe proportional allocation.",
            total_land_acres=land_acres,
            allocated_land_acres=round(sum(a["allocated_acres"] for a in fallback_allocs), 2),
            total_budget=budget,
            utilized_budget=round(sum(a["cost_invested"] for a in fallback_allocs), 2),
            remaining_budget=round(max(0.0, budget - sum(a["cost_invested"] for a in fallback_allocs)), 2),
            total_estimated_revenue=round(sum(a["expected_revenue"] for a in fallback_allocs), 2),
            total_estimated_profit=round(sum(a["profit_est"] for a in fallback_allocs), 2),
            allocations=fallback_allocs,
            reasoning=reasoning,
        )

if __name__ == "__main__":
    print("-" * 60)
    print("Testing SciPy Linear Programming Optimizer with Sample Inputs:")
    print("Inputs: Land = 10 Acres, Budget = Rs. 200,000")
    print("Candidate Recommended Crops: Rice, Cotton, Chickpea, Maize")
    print("-" * 60)
    
    result = optimize_crop_allocation(
        land_acres=10.0,
        budget=200000.0,
        candidate_crops=["rice", "cotton", "chickpea", "maize"]
    )
    
    print(f"Status: {result.status.upper()}")
    print(f"Optimization Message: {result.message}")
    print(f"Total Land: {result.total_land_acres} Acres | Utilized: {result.allocated_land_acres} Acres")
    print(f"Total Budget: Rs. {result.total_budget:,.2f} | Utilized: Rs. {result.utilized_budget:,.2f}")
    print(f"Total Net Profit: Rs. {result.total_estimated_profit:,.2f}")
    print("\nAllocations:")
    for a in result.allocations:
        print(f"  * {a['crop'].ljust(12)}: {a['allocated_acres']} Acres ({a['percentage_of_land']}%) | Cost: Rs. {a['cost_invested']:,.2f} | Profit: Rs. {a['profit_est']:,.2f}")
    print("-" * 60)
