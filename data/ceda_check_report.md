# CEDA Agri Market API Check

- Specification: `https://api.ceda.ashoka.edu.in/documentation/swagger-ui-init.js`
- Auth with configured key: `200`
- Deliberately wrong key: HTTP `401` (rejected: `True`).
- State IDs: `{"Andhra Pradesh": 28, "Telangana": 36, "Maharashtra": 27}`
- Price tests: requested. National price probes only. Andhra Pradesh, Telangana, and Maharashtra price probes are deferred because of the documented 40/hour quota.
- Requested price window: `2025-10-14` through `2026-10-14`.
- Observed price-call rate headers: `RateLimit-Policy=40;w=3600`, remaining 30 at start and 1 at end.

National price data was requested with the documented read-only POST. State price checks were deferred.

| Crop | Commodity ID | Match / candidates | Confidence | National OK | AP OK | TG OK | MH OK | Rows | Latest date | Median modal price (unit unverified) | Issues |
|---|---:|---|---|---|---|---|---|---:|---|---:|---|
| rice | — | Paddy(Dhan)(Basmati) (ID 414), Paddy(Dhan)(Common) (ID 2), Rice (ID 3) | ambiguous | AMBIGUOUS; inspect candidate IDs | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 414: 17; 2: 17; 3: 17 | 414: 2025-10-30; 2: 2025-10-30; 3: 2025-10-30 | 414: 2994.77; 2: 2276.66; 3: 3623.40 | ambiguous; user decision needed; ID 414: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold.; ID 2: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold.; ID 3: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| maize | 4 | Maize (ID 4) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 1990.86 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| chickpea | — | Bengal Gram(Gram)(Whole) (ID 6), Kabuli Chana(Chickpeas-White) (ID 362) | ambiguous | AMBIGUOUS; inspect candidate IDs | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 6: 17; 362: 15 | 6: 2025-10-30; 362: 2025-10-30 | 6: 5836.03; 362: 7482.74 | ambiguous; user decision needed; ID 6: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold.; ID 362: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| kidneybeans | — | Beans (ID 94), Other Pulses (ID 97) | ambiguous | AMBIGUOUS; inspect candidate IDs | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 94: 17; 97: 0 | 94: 2025-10-30; 97: no rows | 94: 6536.73; 97: no price | ambiguous; user decision needed; ID 94: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold.; ID 97: No price rows returned for this period. |
| pigeonpeas | — | Arhar (Tur/Red Gram)(Whole) (ID 49), Red Gram (ID 7) | ambiguous | AMBIGUOUS; inspect candidate IDs | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 49: 17; 7: 0 | 49: 2025-10-30; 7: no rows | 49: 6551.48; 7: no price | ambiguous; user decision needed; ID 49: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold.; ID 7: No price rows returned for this period. |
| mothbeans | — | Mash (ID 60), Moath Dal (ID 95), Moath Dal (ID 258) | ambiguous | AMBIGUOUS; inspect candidate IDs | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 60: 0; 95: 17; 258: 0 | 60: no rows; 95: 2025-10-30; 258: no rows | 60: no price; 95: 4831.45; 258: no price | ambiguous; user decision needed; ID 60: No price rows returned for this period.; ID 95: Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold.; ID 258: No price rows returned for this period. |
| mungbean | 9 | Green Gram (Moong)(Whole) (ID 9) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 6687.15 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| blackgram | 8 | Black Gram (Urd Beans)(Whole) (ID 8) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 6772.85 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| lentil | 63 | Lentil (Masur)(Whole) (ID 63) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 6746.85 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| pomegranate | 190 | Pomegranate (ID 190) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 10001.88 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| banana | 19 | Banana (ID 19) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 3207.34 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| mango | 20 | Mango (ID 20) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 7525.00 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| grapes | 22 | Grapes (ID 22) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 9580.49 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| watermelon | 73 | Water Melon (ID 73) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 2106.81 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| muskmelon | 187 | Karbuja(Musk Melon) (ID 187) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 4085.00 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| apple | 17 | Apple (ID 17) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 6938.48 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| orange | 18 | Orange (ID 18) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 6386.97 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| papaya | 72 | Papaya (ID 72) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 3007.91 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| coconut | 138 | Coconut (ID 138) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 7104.86 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| cotton | 15 | Cotton (ID 15) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 6909.30 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| jute | 16 | Jute (ID 16) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 17 | 2025-10-30 | 8194.44 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |
| coffee | 45 | Coffee (ID 45) | high | YES | deferred_rate_limit | deferred_rate_limit | deferred_rate_limit | 15 | 2025-10-30 | 23800.00 | Latest returned data is 349 days before the requested end date; older than the 30-day warning threshold. |

## Summary

- Crops fully working: 0.
- Crops with sparse or missing price data: kidneybeans, pigeonpeas, mothbeans.
- Crops with stale returned prices: rice, maize, chickpea, kidneybeans, pigeonpeas, mothbeans, mungbean, blackgram, lentil, pomegranate, banana, mango, grapes, watermelon, muskmelon, apple, orange, papaya, coconut, cotton, jute, coffee (warning threshold: 30 days).
- Crops not found: none.
- Ambiguous mappings requiring a user decision: rice, chickpea, kidneybeans, pigeonpeas, mothbeans.
- Do not treat stale CEDA results as current market prices. Use a cached quote only after verifying its freshness; otherwise use the project’s clearly labelled mock price.
- Per-crop fallback recommendations are recorded in `ceda_crop_map.json` and `ceda_check_report.json`.

## Assumptions

- The user-requested 12-month period means the corresponding calendar date one year before today through today, inclusive.
- The user states price units should be Rs per quintal; the OpenAPI response schema itself does not document a unit.
- Candidate-match scores are string-similarity indicators, not verified agronomic equivalence; multiple IDs are left unselected.
- The national run used state_id=0 and the user-authorized documented read-only POST; state-specific price requests were deferred by user choice.
- CEDA authentication uses an Authorization Bearer token as declared by the OpenAPI security scheme.
- State ID 0 is the national aggregate because the prices request-body schema documents that meaning; this value was used for the national price requests.
- A freshness warning is raised for national data older than 30 days before the requested end date; this is a conservative heuristic because the API spec documents no freshness SLA.
