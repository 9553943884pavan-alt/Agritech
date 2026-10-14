import { hasSimulatedPrice } from './priceSimulation.js';

function sourceForPrice(price) {
  if (price?.source === 'tgk_live' || price?.status === 'live') {
    return { key: 'tgk_live', label: 'Live (TGK)' };
  }
  if (price?.source === 'ceda_cached' || price?.status === 'cached') {
    return {
      key: 'ceda_cached',
      label: price.label || 'Historical mandi price (Agmarknet via CEDA)'
    };
  }
  if (price?.source === 'mock' || price?.status === 'mock') {
    return { key: 'mock', label: 'Mock (simulated)' };
  }
  return { key: 'unknown', label: price?.label || 'Price source unavailable' };
}

export function PriceSimulationBanner({
  planIsSimulated = false,
  soilDataIsSimulated = false,
  priceDataIsSimulated = false,
  prices = {}
}) {
  const usesSimulatedPrice = priceDataIsSimulated || hasSimulatedPrice(prices);
  if (!planIsSimulated && !soilDataIsSimulated && !usesSimulatedPrice) {
    return null;
  }

  return (
    <div className="simulation-warning" role="alert">
      SIMULATED DEMO:
      {soilDataIsSimulated && ' One or more soil inputs came from static mock JSON.'}
      {usesSimulatedPrice && ' One or more market prices are mock or proxy prices.'}
      {' '}This plan and its profit estimate are not a real farm or market forecast.
    </div>
  );
}

export function PriceSources({ prices = {} }) {
  return (
    <div className="mandi-price-list">
      {Object.entries(prices || {}).map(([crop, price]) => {
        const source = sourceForPrice(price);
        const rawPrice = price?.price_rs_per_quintal ?? price?.price_per_quintal;
        const numericPrice = Number(rawPrice);
        const priceText = Number.isFinite(numericPrice)
          ? `Rs. ${numericPrice.toLocaleString()} / quintal`
          : 'Price unavailable';
        const asOf = price?.as_of || price?.arrival_date;
        const locationDetail = price?.scope === 'district_aggregate'
          ? `District aggregate for ${price.district} (${price.market_count} markets)`
          : price?.scope === 'state_average'
            ? 'State average'
            : null;
        const note = price?.warning || price?.reason;

        return (
          <div className="mandi-price-item" key={crop}>
            <div className="price-item-heading">
              <strong>{crop}</strong>
              <span className={`price-source-badge price-source-${source.key}`}>
                {source.label}
              </span>
              {price?.proxy && (
                <span
                  className="price-proxy-tag"
                  title="Moth bean dal price used as a proxy"
                >
                  Proxy price
                </span>
              )}
            </div>
            <div className="price-item-details">
              <span>{priceText}</span>
              {asOf && <span>As of {asOf}</span>}
              {locationDetail && <span>{locationDetail}</span>}
              {note && <span>{note}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
