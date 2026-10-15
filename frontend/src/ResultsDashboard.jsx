import { useState } from 'react';
import { PriceSimulationBanner } from './PriceSources.jsx';
import { resultsCopy as copy } from './resultsCopy.js';

const cropColors = [
  '#56B4E9',
  '#E69F00',
  '#009E73',
  '#F0E442',
  '#0072B2',
  '#D55E00',
  '#CC79A7',
  '#999999',
];

const cropIcons = {
  apple: '🍎',
  banana: '🍌',
  blackgram: '🫘',
  chickpea: '🫘',
  coconut: '🥥',
  coffee: '☕',
  cotton: '☁️',
  grapes: '🍇',
  jute: '🌾',
  kidneybeans: '🫘',
  lentil: '🫘',
  maize: '🌽',
  mango: '🥭',
  mothbeans: '🫘',
  muskmelon: '🍈',
  mungbean: '🫘',
  orange: '🍊',
  papaya: '🍈',
  pigeonpeas: '🫘',
  pomegranate: '🍎',
  rice: '🌾',
  watermelon: '🍉',
};

const numberFormatter = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 });

const formatNumber = (value, maximumFractionDigits = 1) =>
  maximumFractionDigits === 1
    ? numberFormatter.format(Number(value || 0))
    : new Intl.NumberFormat('en-IN', { maximumFractionDigits }).format(Number(value || 0));

const formatRupees = (value) => `Rs. ${formatNumber(value, 0)}`;

const cropKey = (crop) => String(crop || '').trim().toLowerCase().replace(/[\s_-]+/g, '');
const cropIcon = (crop) => cropIcons[cropKey(crop)] || '🌱';
const normalizeDisplayText = (value) => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
const displayLocation = (value) => {
  const parts = normalizeDisplayText(value)
    .split(',')
    .map((part) => part.trim().replace(/\s+District$/i, ''));
  return parts.filter((part, index) => (
    part && !parts.slice(0, index).some((previous) => previous.toLowerCase() === part.toLowerCase())
  )).join(', ');
};
const cropName = (crop, index) => {
  const raw = String(crop || `${copy.crop} ${index + 1}`).trim();
  return copy.cropNames[cropKey(raw)] || raw.replace(/\b\w/g, (letter) => letter.toUpperCase());
};
const optionalNumber = (value) => {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};
const cropColor = (crop, index) =>
  cropColors[
    Object.keys(cropIcons).sort().indexOf(cropKey(crop)) >= 0
      ? Object.keys(cropIcons).sort().indexOf(cropKey(crop)) % cropColors.length
      : index % cropColors.length
  ];

function getPriceLabel(price = {}) {
  if (price.source === 'ceda_cached' || price.status === 'cached') return copy.sourceHistorical;
  if (price.source === 'tgk_live' || price.status === 'live') return copy.sourceLive;
  if (price.source === 'mock' || price.status === 'mock') return copy.sourceMock;
  return price.label || copy.priceUnavailable;
}

function cropItem(item, index) {
  return {
    ...item,
    crop: cropName(item.crop, index),
    acres: optionalNumber(item.allocated_acres) ?? 0,
    profit: optionalNumber(item.profit_est),
    yieldTonnes: optionalNumber(item.expected_yield_tonnes),
    revenue: optionalNumber(item.expected_revenue),
    cost: optionalNumber(item.cost_invested),
    color: cropColor(item.crop, index),
  };
}

function aggregateSmallCrops(items) {
  if (items.length <= 6) return items;
  const sorted = [...items].sort((left, right) => right.acres - left.acres);
  const visible = sorted.slice(0, 5);
  const small = sorted.slice(5);
  const acres = small.reduce((sum, item) => sum + item.acres, 0);
  const sumIfComplete = (key) => small.every((item) => item[key] !== null)
    ? small.reduce((sum, item) => sum + item[key], 0)
    : null;
  const profit = sumIfComplete('profit');
  const yieldTonnes = sumIfComplete('yieldTonnes');
  const revenue = sumIfComplete('revenue');
  const cost = sumIfComplete('cost');
  return [
    ...visible,
    {
      crop: copy.other,
      acres,
      profit,
      yieldTonnes,
      revenue,
      cost,
      color: '#999999',
      otherCrops: small.map((item) => item.crop),
    },
  ];
}

function ChartTable({ title, rows, columns, visible, onToggle }) {
  return (
    <div className="chart-table-control">
      <button
        className="chart-table-toggle"
        type="button"
        aria-expanded={visible}
        onClick={onToggle}
      >
        {visible ? copy.hideTable : copy.showTable}
      </button>
      {visible && (
        <div className="chart-table-scroll">
          <table>
            <caption>{title}</caption>
            <thead>
              <tr>{columns.map((column) => <th key={column.key} scope="col">{column.label}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.crop || row.label}-${index}`}>
                  {columns.map((column) => (
                    <td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ChartSection({ title, description, children, rows, columns, ariaLabel }) {
  const [showTable, setShowTable] = useState(false);
  return (
    <section className="result-chart-card" aria-label={ariaLabel || `${title}. ${description}`}>
      <div className="result-chart-heading">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
      </div>
      {children}
      <ChartTable
        title={title}
        rows={rows}
        columns={columns}
        visible={showTable}
        onToggle={() => setShowTable((visible) => !visible)}
      />
    </section>
  );
}

function DonutChart({ items }) {
  const [selectedCrop, setSelectedCrop] = useState(null);
  const total = items.reduce((sum, item) => sum + item.acres, 0);
  const radius = 64;
  const circumference = 2 * Math.PI * radius;
  const segments = items.reduce((result, item) => {
    const share = total > 0 ? Math.max(0, item.acres / total) : 0;
    const previous = result[result.length - 1];
    const offset = previous ? previous.offset + previous.dash : 0;
    result.push({ ...item, share, dash: share * circumference, offset });
    return result;
  }, []);

  return (
    <div className="donut-layout">
      <svg
        className="land-donut"
        viewBox="0 0 180 180"
        role="group"
        aria-label={`${copy.landSplit}: ${items.map((item) => `${item.crop} ${formatNumber(item.acres)} ${copy.acres.toLowerCase()}`).join(', ')}`}
      >
        <circle cx="90" cy="90" r={radius} fill="none" stroke="rgba(255,255,255,.14)" strokeWidth="28" />
        {total > 0 && segments.map((segment) => (
          <circle
            key={segment.crop}
            cx="90"
            cy="90"
            r={radius}
            fill="none"
            stroke={segment.color}
            strokeWidth="28"
            strokeDasharray={`${segment.dash} ${circumference - segment.dash}`}
            strokeDashoffset={-segment.offset}
            transform="rotate(-90 90 90)"
            tabIndex="0"
            role="button"
            aria-label={`${segment.crop}: ${formatNumber(segment.acres)} ${copy.acres.toLowerCase()}, ${formatNumber(segment.share * 100)} ${copy.percent}`}
            onClick={() => setSelectedCrop(selectedCrop === segment.crop ? null : segment.crop)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                setSelectedCrop(selectedCrop === segment.crop ? null : segment.crop);
              }
            }}
          />
        ))}
        <text x="90" y="85" textAnchor="middle" className="donut-total">{formatNumber(total)}</text>
        <text x="90" y="107" textAnchor="middle" className="donut-unit">{copy.acres}</text>
      </svg>
      <div className="donut-legend" aria-label={copy.landSplit}>
        {segments.map((item, index) => (
          <button
            className={`donut-legend-row${item.share < 0.04 ? ' donut-small-slice' : ''}`}
            key={item.crop}
            type="button"
            onClick={() => setSelectedCrop(selectedCrop === item.crop ? null : item.crop)}
            aria-pressed={selectedCrop === item.crop}
          >
            <span className="crop-swatch" style={{ '--crop-color': item.color }} aria-hidden="true" />
            <span className="crop-icon" aria-hidden="true">{cropIcon(item.crop)}</span>
            <span className="crop-legend-name">
              {item.crop}
              {item.share < 0.04 && <span className="leader-label" aria-hidden="true">↗</span>}
            </span>
            <strong>{formatNumber(item.acres)} {copy.acresShort}</strong>
            <span>{formatNumber(item.share * 100)}%</span>
            <span className="sr-only">{`${copy.slice} ${index + 1}`}</span>
          </button>
        ))}
      </div>
      {selectedCrop && (
        <p className="chart-tap-detail" role="status">
          {selectedCrop}: {formatNumber(segments.find((item) => item.crop === selectedCrop)?.acres)} {copy.acres}
        </p>
      )}
    </div>
  );
}

function HorizontalBars({ items, valueFor, valueLabel, secondaryFor, unit, allowNegative = false }) {
  const [selectedCrop, setSelectedCrop] = useState(null);
  const values = items.map((item) => valueFor(item));
  const maximum = Math.max(...values.map((value) => Math.abs(value)), 0);

  return (
    <div className="horizontal-bars">
      {items.map((item, index) => {
        const value = valueFor(item);
        const width = maximum > 0 ? Math.max(Math.abs(value) / maximum * 100, value === 0 ? 0 : 2) : 0;
        const isNegative = value < 0;
        const selected = selectedCrop === item.crop;
        return (
          <button
            key={`${item.crop}-${index}`}
            type="button"
            className={`bar-row${isNegative ? ' bar-negative' : ''}${selected ? ' bar-selected' : ''}`}
            onClick={() => setSelectedCrop(selected ? null : item.crop)}
            aria-pressed={selected}
            aria-label={`${item.crop}: ${valueLabel(item)}${secondaryFor ? `, ${secondaryFor(item)}` : ''}`}
          >
            <span className="bar-crop-label">
              <span className="crop-icon" aria-hidden="true">{cropIcon(item.crop)}</span>
              <span className="crop-name-wrap">{item.crop}</span>
            </span>
            <span className="bar-track">
              <span
                className={`bar-fill${allowNegative && isNegative ? ' bar-fill-negative' : ''}`}
                style={{ width: `${width}%`, '--crop-color': item.color }}
              />
              <span className="bar-value">{valueLabel(item)}</span>
            </span>
            {secondaryFor && <span className="bar-secondary">{secondaryFor(item)}</span>}
            {unit && <span className="bar-unit">{unit}</span>}
          </button>
        );
      })}
      {selectedCrop && <p className="chart-tap-detail" role="status">{selectedCrop}</p>}
    </div>
  );
}

function PriceChart({ items, prices }) {
  const [showTable, setShowTable] = useState(false);
  const [selectedCrop, setSelectedCrop] = useState(null);
  const rows = items.flatMap((item) => {
    const price = prices[cropKey(item.crop)];
    const rawValue = price?.price_rs_per_quintal ?? price?.price_per_quintal;
    const value = Number(rawValue);
    if (rawValue == null || rawValue === '' || !Number.isFinite(value)) return [];
    return {
      ...item,
      price: price || {},
      value,
      simulated: price.proxy === true || price.is_simulated === true
        || price.source === 'mock' || price.status === 'mock',
      label: getPriceLabel(price),
    };
  });
  const maxPrice = Math.max(...rows.map((row) => row.value), 0);
  return (
    <section
      className="result-chart-card"
      aria-label={`${copy.pricePerQuintal}. ${copy.priceChartDescription}`}
    >
      <div className="result-chart-heading"><div><h3>{copy.pricePerQuintal}</h3><p>{copy.priceChartDescription}</p></div></div>
      {rows.length ? (
        <>
          <div className="horizontal-bars price-bars">
            {rows.map((item) => {
              const selected = selectedCrop === item.crop;
              return (
                <button
                  className={`price-bar-row${selected ? ' bar-selected' : ''}`}
                  type="button"
                  key={item.crop}
                  aria-pressed={selected}
                  aria-label={`${item.crop}: ${formatRupees(item.value)} ${copy.priceUnit}. ${item.label}${item.price.proxy ? `, ${copy.sourceProxy}` : ''}`}
                  onClick={() => setSelectedCrop(selected ? null : item.crop)}
                >
                  <span className="bar-crop-label">
                    <span className="crop-icon" aria-hidden="true">{cropIcon(item.crop)}</span>
                    <span className="crop-name-wrap">{item.crop}</span>
                  </span>
                  <span className="bar-track">
                    <span
                      className={`bar-fill${item.simulated ? ' bar-fill-simulated' : ''}`}
                      style={{
                        width: `${maxPrice > 0 ? Math.max(item.value / maxPrice * 100, 2) : 0}%`,
                        '--crop-color': item.color,
                      }}
                    />
                    <span className="bar-value">{formatRupees(item.value)}</span>
                  </span>
                  <span className="price-source-badge">{item.label}</span>
                  {item.price.proxy && <span className="price-proxy-tag">{copy.sourceProxy}</span>}
                  {(item.price.as_of || item.price.arrival_date) && (
                    <span className="bar-secondary">{item.price.as_of || item.price.arrival_date}</span>
                  )}
                  {(item.price.warning || item.price.reason) && (
                    <span className="bar-secondary">{item.price.warning || item.price.reason}</span>
                  )}
                </button>
              );
            })}
          </div>
          {selectedCrop && (
            <p className="chart-tap-detail" role="status">
              {(rows.find((row) => row.crop === selectedCrop)?.price.warning
                || rows.find((row) => row.crop === selectedCrop)?.price.reason)
                || `${rows.find((row) => row.crop === selectedCrop)?.label}${rows.find((row) => row.crop === selectedCrop)?.price.as_of
                  || rows.find((row) => row.crop === selectedCrop)?.price.arrival_date
                  ? `, ${copy.priceDate}: ${rows.find((row) => row.crop === selectedCrop).price.as_of || rows.find((row) => row.crop === selectedCrop).price.arrival_date}`
                  : ''}`}
            </p>
          )}
        </>
      ) : <p className="chart-note">{copy.noPriceData}</p>}
      <div className="chart-table-control">
        <button className="chart-table-toggle" type="button" aria-expanded={showTable} onClick={() => setShowTable((value) => !value)}>
          {showTable ? copy.hideTable : copy.showTable}
        </button>
        {showTable && (
          <div className="chart-table-scroll">
            <table>
              <caption>{copy.pricePerQuintal}</caption>
              <thead><tr><th scope="col">{copy.crop}</th><th scope="col">{copy.priceUnit}</th><th scope="col">{copy.sourceBadge}</th><th scope="col">{copy.priceDate}</th></tr></thead>
              <tbody>
                {rows.map((row) => <tr key={row.crop}><td>{cropIcon(row.crop)} {row.crop}</td><td>{formatRupees(row.value)}</td><td>{row.label}</td><td>{row.price.as_of || row.price.arrival_date || '—'}</td></tr>)}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function StackedShares({ items }) {
  const [selected, setSelected] = useState(null);
  const totalAcres = items.reduce((sum, item) => sum + item.acres, 0);
  const profitBasis = items.reduce((sum, item) => sum + Math.max(item.profit, 0), 0);
  const landSegments = items.map((item) => ({
    ...item,
    share: totalAcres > 0 ? item.acres / totalAcres * 100 : 0,
  }));
  const profitSegments = items.map((item) => ({
    ...item,
    share: profitBasis > 0 ? Math.max(item.profit, 0) / profitBasis * 100 : 0,
  }));

  return (
    <div className="stacked-chart" role="group" aria-label={`${copy.landVsProfit}: ${items.map((item) => `${item.crop}: ${copy.landWord} ${formatNumber(totalAcres > 0 ? item.acres / totalAcres * 100 : 0)} ${copy.percent}, ${copy.profitWord} ${formatNumber(profitBasis > 0 ? Math.max(item.profit, 0) / profitBasis * 100 : 0)} ${copy.percent}`).join('; ')}`}>
      {[{ name: copy.land, segments: landSegments }, { name: copy.profit, segments: profitSegments }].map((bar) => (
        <div className="stacked-row" key={bar.name}>
          <strong>{bar.name}</strong>
          <div className="stacked-track" aria-label={bar.name}>
            {bar.segments.map((segment) => segment.share > 0 && (
              <button
                key={segment.crop}
                type="button"
                className="stacked-segment"
                style={{ width: `${segment.share}%`, '--crop-color': segment.color }}
                aria-label={`${bar.name}, ${segment.crop}: ${formatNumber(segment.share)} ${copy.percent}`}
                aria-pressed={selected?.crop === segment.crop && selected?.bar === bar.name}
                onClick={() => setSelected(
                  selected?.crop === segment.crop && selected?.bar === bar.name
                    ? null
                    : { bar: bar.name, crop: segment.crop, share: segment.share },
                )}
              />
            ))}
          </div>
        </div>
      ))}
      {selected && (
        <p className="chart-tap-detail" role="status">
          {selected.crop}: {formatNumber(selected.share)}% {selected.bar.toLowerCase()}
        </p>
      )}
      <div className="stacked-legend">
        {items.map((item) => {
          const landShare = totalAcres > 0 ? item.acres / totalAcres * 100 : 0;
          const profitShare = profitBasis > 0 ? Math.max(item.profit, 0) / profitBasis * 100 : 0;
          return (
            <div className="stacked-legend-row" key={item.crop}>
              <span className="crop-swatch" style={{ '--crop-color': item.color }} />
              <span><span className="crop-icon" aria-hidden="true">{cropIcon(item.crop)}</span> {item.crop}</span>
              <span>{formatNumber(landShare)}% {copy.land.toLowerCase()}</span>
              <span>{formatNumber(profitShare)}% {copy.profit.toLowerCase()}</span>
              {item.profit <= 0 && <strong>{item.profit < 0 ? copy.loss : copy.zeroProfit}</strong>}
            </div>
          );
        })}
      </div>
      {profitBasis === 0 && <p className="chart-note">{copy.positiveShareUnavailable}</p>}
    </div>
  );
}

function DataUsedCard({ result }) {
  const regional = result.regional_data || {};
  const provenance = result.soil_data_provenance || {};
  const location = displayLocation(result.resolved_location);
  const sourceName = (feature) => {
    const status = provenance[feature]?.status;
    if (status === 'mock') return copy.mock;
    if (status === 'api_estimate') return copy.publicEstimate;
    if (status === 'user_measurement') return copy.farmerTest;
    return '';
  };

  return (
    <section className="data-used-card">
      <h3>{copy.dataUsed}</h3>
      <div className="data-badges">
        {location && <span>{copy.location}: {location}</span>}
        {regional.temperature != null && <span>{copy.temperature}: {formatNumber(regional.temperature)}°C</span>}
        {regional.rainfall != null && <span>{copy.rainfall}: {formatNumber(regional.rainfall)} mm</span>}
        {[
          ['nitrogen', 'N'],
          ['phosphorus', 'P'],
          ['potassium', 'K'],
          ['ph', 'pH'],
        ].map(([feature, label]) => regional[feature] != null && (
          <span key={feature}>{label}: {regional[feature]} ({sourceName(feature) || copy.farmerTest})</span>
        ))}
      </div>
      {result.soil_source_details?.note && <p className="soil-source-note">{result.soil_source_details.note}</p>}
      {result.soil_source_details?.soilgrids?.api_error && (
        <p className="soil-source-note">{copy.soilFallbackWarning}</p>
      )}
      {result.soil_health_summary && (
        <details className="soil-summary">
          <summary>{copy.soilCategories} ({result.soil_health_summary.district}, {result.soil_health_summary.cycle})</summary>
          <pre>{JSON.stringify(result.soil_health_summary.category_counts, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

function LandProfitChart({ items }) {
  const totalAcres = items.reduce((sum, crop) => sum + crop.acres, 0);
  const positiveProfit = items.reduce((sum, crop) => sum + Math.max(crop.profit, 0), 0);
  const rows = items.map((item) => ({
    ...item,
    landShare: totalAcres > 0 ? item.acres / totalAcres * 100 : 0,
    profitShare: positiveProfit > 0 ? Math.max(item.profit, 0) / positiveProfit * 100 : 0,
  }));
  return (
    <ChartSection
      title={copy.landVsProfit}
      description={copy.landProfitChartDescription}
      rows={rows}
      columns={[
        { key: 'crop', label: copy.crop },
        { key: 'landShare', label: `${copy.land} (%)`, render: (row) => `${formatNumber(row.landShare)}%` },
        { key: 'profitShare', label: `${copy.profit} (%)`, render: (row) => `${formatNumber(row.profitShare)}%` },
        { key: 'profit', label: copy.profit, render: (row) => formatRupees(row.profit) },
      ]}
    >
      <StackedShares items={items} />
    </ChartSection>
  );
}

function BudgetChart({ spent, available }) {
  const percent = Math.min(100, spent / available * 100);
  const rows = [{ label: copy.budgetUse, spent, available }];
  return (
    <ChartSection
      title={copy.budgetUse}
      description={`${formatRupees(spent)} ${copy.of} ${formatRupees(available)} ${copy.spent}.`}
      ariaLabel={`${copy.budgetUse}: ${formatRupees(spent)} ${copy.of} ${formatRupees(available)}`}
      rows={rows}
      columns={[
        { key: 'label', label: copy.chart },
        { key: 'spent', label: copy.spent, render: (row) => formatRupees(row.spent) },
        { key: 'available', label: copy.available, render: (row) => formatRupees(row.available) },
      ]}
    >
      <p className="budget-values">{formatRupees(spent)} {copy.of} {formatRupees(available)} {copy.spent}</p>
      <div
        className="budget-progress"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label={copy.budgetUse}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
    </ChartSection>
  );
}

const optimizerDecisionCopy = {
  fallback_assignment: copy.optimizerDecisionFallback,
  nonpositive_margin: copy.optimizerDecisionNonpositive,
  not_selected_in_mix: copy.optimizerDecisionNotSelected,
  crop_limit: copy.optimizerDecisionCropLimit,
  positive_profit_mix: copy.optimizerDecisionPositive,
};

function ClassifierReasoning({ reasoning }) {
  if (!Array.isArray(reasoning?.crops) || !reasoning.crops.length) return null;
  return (
    <section className="reasoning-block" aria-label={copy.classifierReasoning}>
      <h3>{copy.classifierReasoning}</h3>
      <p><strong>{copy.optimizerMethod}:</strong> {copy.classifierMethod}</p>
      <p>{copy.classifierScoreNote}</p>
      <p>{copy.classifierAttributionNote}</p>
      <div className="reasoning-crop-list">
        {reasoning.crops.map((crop) => {
          const score = optionalNumber(crop.score);
          const effects = (Array.isArray(crop.feature_contributions) ? crop.feature_contributions : [])
            .map((item) => ({
              ...item,
              change: Number(item.shap_value) * 100,
            }))
            .filter((item) => Number.isFinite(item.change) && Math.abs(item.change) >= 0.01);
          const raised = effects.filter((item) => item.change > 0)
            .sort((left, right) => right.change - left.change).slice(0, 2);
          const lowered = effects.filter((item) => item.change < 0)
            .sort((left, right) => left.change - right.change).slice(0, 2);
          const relevantEffects = [...raised, ...lowered];
          return (
            <article className="reasoning-crop-card" key={crop.crop}>
              <h4>
                <span className="crop-icon" aria-hidden="true">{cropIcon(crop.crop)}</span>
                {cropName(crop.crop, crop.rank - 1)}
                <span className="reasoning-rank">#{crop.rank}</span>
              </h4>
              <p>
                {copy.classifierScore}: <strong>{score === null ? '—' : `${formatNumber(score * 100)}%`}</strong>
              </p>
              {relevantEffects.length ? (
                <ul className="reasoning-effect-list">
                  {relevantEffects.map((effect) => (
                    <li key={effect.feature}>
                      <strong>{copy.reasoningFeatures[effect.feature] || effect.feature}</strong>
                      {' '}({effect.value == null ? '—' : formatNumber(effect.value)}) — {effect.change > 0
                        ? copy.positiveModelEffect
                        : copy.negativeModelEffect}
                      {' '}({effect.change > 0 ? '+' : ''}{formatNumber(effect.change)} {copy.percentagePoints})
                    </li>
                  ))}
                </ul>
              ) : <p>{copy.noFeatureEffects}</p>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function OptimizerReasoning({ reasoning }) {
  if (!Array.isArray(reasoning?.crops) || !reasoning.crops.length) return null;
  const constraints = reasoning.constraints || {};
  return (
    <section className="reasoning-block" aria-label={copy.optimizerReasoning}>
      <h3>{copy.optimizerReasoning}</h3>
      <p>
        <strong>{copy.optimizerMethod}:</strong>{' '}
        {reasoning.method === 'linear_program_highs'
          ? copy.optimizerMethodLinearProgram
          : copy.optimizerMethodFallback}
      </p>
      <p>{reasoning.objective === 'maximize_net_profit'
        ? copy.optimizerObjective
        : copy.optimizerFallbackObjective}</p>
      <p>{copy.optimizerScope}</p>
      <ul className="reasoning-constraint-list">
        <li>
          {copy.landConstraint}: {formatNumber(constraints.land_acres_used)} / {formatNumber(constraints.land_acres_available)} {copy.acres.toLowerCase()}
          {' '}— {constraints.land_limit_reached ? copy.limitReached : copy.limitNotReached}
        </li>
        <li>
          {copy.budgetConstraint}: {formatRupees(constraints.budget_used)} / {formatRupees(constraints.budget_available)}
          {' '}— {constraints.budget_limit_reached ? copy.limitReached : copy.limitNotReached}
        </li>
        <li>
          {copy.cropCapConstraint}: {formatNumber(constraints.max_acres_per_crop)} {copy.acres.toLowerCase()}
          {' '}({formatNumber(Number(constraints.crop_share_limit) * 100)}%)
        </li>
      </ul>
      <div className="reasoning-crop-list">
        {reasoning.crops.map((crop) => {
          const profitPerAcre = Number(crop.profit_per_acre);
          return (
            <article className="reasoning-crop-card" key={crop.crop}>
              <h4>
                <span className="crop-icon" aria-hidden="true">{cropIcon(crop.crop)}</span>
                {cropName(crop.crop)}
              </h4>
              <dl>
                <div><dt>{copy.optimizerYield}</dt><dd>{formatNumber(crop.yield_tonnes_per_hectare)} {copy.tonnesPerHectare}</dd></div>
                <div><dt>{copy.optimizerYieldPerAcre}</dt><dd>{formatNumber(crop.yield_quintals_per_acre)} {copy.quintalsPerAcre}</dd></div>
                <div><dt>{copy.optimizerPrice}</dt><dd>{formatRupees(crop.price_per_quintal)} / {copy.quintals}</dd></div>
                <div><dt>{copy.optimizerCost}</dt><dd>{formatRupees(crop.cost_per_acre)}</dd></div>
                <div><dt>{copy.optimizerRevenue}</dt><dd>{formatRupees(crop.revenue_per_acre)}</dd></div>
                <div>
                  <dt>{copy.optimizerProfit}</dt>
                  <dd>{profitPerAcre < 0 ? `${copy.lossPerAcre}: ` : ''}{formatRupees(profitPerAcre)}</dd>
                </div>
                <div><dt>{copy.optimizerAllocation}</dt><dd>{formatNumber(crop.allocated_acres)} {copy.acres.toLowerCase()}</dd></div>
                <div><dt>{copy.optimizerProfitContribution}</dt><dd>{formatRupees(crop.profit_contribution)}</dd></div>
              </dl>
              {optimizerDecisionCopy[crop.decision_note]
                ? <p>{optimizerDecisionCopy[crop.decision_note]}</p>
                : Number(crop.allocated_acres) <= 0 && <p>{copy.noLandAssigned}</p>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function AiReasoning({ reasoning }) {
  if (!reasoning) {
    return (
      <section className="reasoning-block ai-reasoning-block" aria-label={copy.aiReasoningTitle}>
        <h3>{copy.aiReasoningTitle}</h3>
        <p role="status">{copy.aiReasoningMissing}</p>
      </section>
    );
  }
  if (reasoning.status !== 'ready') {
    return (
      <section className="reasoning-block ai-reasoning-block" aria-label={copy.aiReasoningTitle}>
        <h3>{copy.aiReasoningTitle}</h3>
        <p role="status">
          {reasoning.error_code === 'missing_api_key'
            ? copy.aiReasoningNotConfigured
            : copy.aiReasoningUnavailable}
        </p>
      </section>
    );
  }

  const classifierReasons = new Map(
    (Array.isArray(reasoning.classifier_reasons) ? reasoning.classifier_reasons : [])
      .map((item) => [item.crop, item.reason]),
  );
  const allocationReasons = new Map(
    (Array.isArray(reasoning.allocation_reasons) ? reasoning.allocation_reasons : [])
      .map((item) => [item.crop, item.reason]),
  );

  return (
    <section className="reasoning-block ai-reasoning-block" aria-label={copy.aiReasoningTitle}>
      <h3>{copy.aiReasoningTitle}</h3>
      <p>{copy.aiReasoningDisclaimer}</p>
      <h4>{copy.aiClassifierSummary}</h4>
      <p>{reasoning.classifier_summary}</p>
      <div className="reasoning-crop-list">
        {[...classifierReasons.entries()].map(([crop, reason]) => (
          <article className="reasoning-crop-card" key={crop}>
            <h4><span className="crop-icon" aria-hidden="true">{cropIcon(crop)}</span>{cropName(crop)}</h4>
            <p><strong>{copy.aiCropExplanation}:</strong> {reason}</p>
          </article>
        ))}
      </div>
      <h4>{copy.aiOptimizerSummary}</h4>
      <p>{reasoning.optimizer_summary}</p>
      <div className="reasoning-crop-list">
        {[...allocationReasons.entries()].map(([crop, reason]) => (
          <article className="reasoning-crop-card" key={crop}>
            <h4><span className="crop-icon" aria-hidden="true">{cropIcon(crop)}</span>{cropName(crop)}</h4>
            <p><strong>{copy.aiCropExplanation}:</strong> {reason}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function ResultsDashboard({
  result,
  priceDetails,
  priceDataIsSimulated,
  landInput,
  budgetInput,
  onReset,
}) {
  const allocations = Array.isArray(result?.allocations) ? result.allocations : [];
  const items = aggregateSmallCrops(allocations.map(cropItem));
  const allItems = allocations.map(cropItem);
  const landItems = items.filter((item) => item.acres > 0);
  const totalAcres = landItems.reduce((sum, item) => sum + item.acres, 0);
  const totalProfit = optionalNumber(result?.total_estimated_profit);
  const validTotalProfit = totalProfit !== null;
  const summaryItems = allItems.filter((item) => item.acres > 0);
  const recommendedCrops = Array.isArray(result?.top_crops_recommended)
    ? result.top_crops_recommended.map(cropName)
    : [];
  const optimizedCrops = Array.isArray(result?.optimized_crops)
    ? result.optimized_crops.map(cropName)
    : [];
  const optimizedKeys = new Set(optimizedCrops.map(cropKey));
  const skippedRecommendations = recommendedCrops.filter((crop) => !optimizedKeys.has(cropKey(crop)));
  const summary = summaryItems.length
    ? `${copy.summaryLead} ${summaryItems.map((item) => `${item.crop} ${copy.on} ${formatNumber(item.acres)} ${copy.acres.toLowerCase()}`).join(', ')}. ${copy.estimatedProfit}: ${validTotalProfit ? formatRupees(totalProfit) : copy.noProfit}.`
    : copy.noPlan;
  const moreDetailsLabel = copy.moreDetails;
  const budgetAvailable = Number(budgetInput);
  const budgetSpent = optionalNumber(result?.utilized_budget);
  const hasBudgetInfo = budgetInput !== '' && Number.isFinite(budgetAvailable) && budgetAvailable > 0
    && budgetSpent !== null && budgetSpent >= 0;
  const hasCompleteProfit = items.length > 0 && items.every((item) => item.profit !== null);
  const profitSorted = hasCompleteProfit
    ? [...items].sort((left, right) => right.profit - left.profit)
    : [];
  const priceMap = Object.fromEntries(
    Object.entries(priceDetails || {}).map(([crop, details]) => [cropKey(crop), details]),
  );
  const yieldRows = items.filter((item) => item.yieldTonnes !== null).map((item) => ({
    ...item,
    quintals: item.yieldTonnes * 10,
  }));
  const displayMessage = normalizeDisplayText(result.message)
    .replace(normalizeDisplayText(result.resolved_location), displayLocation(result.resolved_location));

  return (
    <div className="results-dashboard fade-in">
      <div className="success-banner">{displayMessage}</div>
      <PriceSimulationBanner
        planIsSimulated={result.plan_is_simulated}
        soilDataIsSimulated={result.soil_data_is_simulated}
        priceDataIsSimulated={priceDataIsSimulated}
        prices={priceDetails}
      />
      {skippedRecommendations.length > 0 && (
        <div className="integration-warning" role="status">
          <p><strong>{copy.cropsSkipped}</strong> {skippedRecommendations.join(', ')}.</p>
          {optimizedCrops.length > 0 && (
            <p><strong>{copy.optimizedCrops}</strong> {optimizedCrops.join(', ')}.</p>
          )}
        </div>
      )}

      <p className="results-summary" aria-live="polite">{summary}</p>
      <div className="results-input-reminder">
        {landInput && <span>{copy.submittedLand}: <strong>{formatNumber(landInput)} {copy.acres.toLowerCase()}</strong></span>}
        {budgetInput && <span>{copy.submittedBudget}: <strong>{formatRupees(budgetAvailable)}</strong></span>}
      </div>

      <div className="result-core-charts">
        <ChartSection
          title={copy.landSplit}
          description={copy.landChartDescription}
          rows={landItems.map((item) => ({
            ...item,
            share: totalAcres > 0 ? item.acres / totalAcres * 100 : 0,
          }))}
          columns={[
            { key: 'crop', label: copy.crop },
            { key: 'acres', label: copy.acres },
            { key: 'share', label: `${copy.share} (%)`, render: (row) => `${formatNumber(row.share)}%` },
          ]}
        >
          {landItems.length ? <DonutChart items={landItems} /> : <p className="chart-note">{copy.noData}</p>}
        </ChartSection>

        {hasCompleteProfit ? (
          <ChartSection
            title={copy.profitByCrop}
            description={copy.profitChartDescription}
            rows={profitSorted}
            columns={[
              { key: 'crop', label: copy.crop },
              { key: 'profit', label: copy.profit, render: (row) => formatRupees(row.profit) },
              { key: 'perAcre', label: `Rs. ${copy.perAcre}`, render: (row) => row.acres ? `${formatRupees(row.profit / row.acres)} / ${copy.acres.toLowerCase()}` : '—' },
            ]}
          >
            <HorizontalBars
              items={profitSorted}
              valueFor={(item) => item.profit}
              valueLabel={(item) => `${item.profit < 0 ? '−' : ''}${formatRupees(Math.abs(item.profit))}`}
              secondaryFor={(item) => item.acres ? `${formatRupees(item.profit / item.acres)} ${copy.perAcre}` : copy.zeroProfit}
              allowNegative
            />
          </ChartSection>
        ) : (
          <p className="not-built-note"><strong>{copy.notBuilt}:</strong> {copy.noCropProfit}</p>
        )}
      </div>

      <section className="total-profit-card" aria-label={`${copy.estimatedProfit}: ${validTotalProfit ? formatRupees(totalProfit) : copy.noProfit}`}>
        <span>{copy.estimated}</span>
        <strong>{validTotalProfit ? formatRupees(totalProfit) : copy.noProfit}</strong>
      </section>

      <AiReasoning reasoning={result.ai_reasoning} />

      {(result.classifier_reasoning || result.optimizer_reasoning) && (
        <details className="reasoning-details">
          <summary>{copy.reasoningTitle}</summary>
          <div className="reasoning-content">
            <ClassifierReasoning reasoning={result.classifier_reasoning} />
            <OptimizerReasoning reasoning={result.optimizer_reasoning} />
          </div>
        </details>
      )}

      <details className="more-details">
        <summary>{moreDetailsLabel}</summary>
        <div className="more-details-content">
          <ChartSection
            title={copy.expectedYield}
            description={`${copy.yieldChartDescription} (${copy.quintals}).`}
            rows={yieldRows}
            columns={[
              { key: 'crop', label: copy.crop },
              { key: 'quintals', label: `${copy.expectedYield} (${copy.quintals})`, render: (row) => `${formatNumber(row.quintals)} ${copy.quintals}` },
            ]}
          >
            <HorizontalBars
              items={yieldRows}
              valueFor={(item) => item.quintals}
              valueLabel={(item) => formatNumber(item.quintals)}
              unit={copy.quintals}
            />
            {!yieldRows.length && <p className="chart-note">{copy.noYieldData}</p>}
            {yieldRows.length < items.length && <p className="chart-note">{copy.noCropYield}</p>}
          </ChartSection>

          <PriceChart items={allItems} prices={priceMap} />
          {hasCompleteProfit
            ? <LandProfitChart items={items} />
            : <p className="not-built-note"><strong>{copy.notBuilt}:</strong> {copy.noCropProfit}</p>}

          {hasBudgetInfo ? (
            <BudgetChart spent={budgetSpent} available={budgetAvailable} />
          ) : (
            <p className="not-built-note"><strong>{copy.notBuilt}:</strong> {copy.budgetNotReturned}</p>
          )}

          <p className="not-built-note"><strong>{copy.notBuilt}:</strong> {copy.weatherSeriesNotReturned}</p>
        </div>
      </details>

      <DataUsedCard result={result} />
      {result.soil_health_source_error && (
        <p className="soil-source-note">{copy.soilUnavailable}</p>
      )}
      <button className="reset-button" type="button" onClick={onReset}>{copy.planAnotherFarm}</button>
    </div>
  );
}
