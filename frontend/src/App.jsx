import { useState } from 'react';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'https://agritech-1euc.onrender.com';

function App() {
  const [location, setLocation] = useState('');
  const [budget, setBudget] = useState('');
  const [land, setLand] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [isListening, setIsListening] = useState(false);
  const [soilTestRequired, setSoilTestRequired] = useState(false);
  const [soilTest, setSoilTest] = useState({
    nitrogen: '',
    phosphorus: '',
    potassium: '',
    ph: ''
  });
  const [soilSummary, setSoilSummary] = useState(null);
  const [soilSourceError, setSoilSourceError] = useState('');
  const [mandiFailures, setMandiFailures] = useState(null);
  const [error, setError] = useState('');

  const updateLocation = (value) => {
    setLocation(value);
    setSoilTestRequired(false);
    setSoilTest({ nitrogen: '', phosphorus: '', potassium: '', ph: '' });
    setSoilSummary(null);
    setSoilSourceError('');
    setMandiFailures(null);
    setError('');
  };

  // Web Speech API for Voice Input
  const handleVoiceInput = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      alert("Your browser does not support Voice Input. Please use Chrome.");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = 'en-IN'; // Can be set to hi-IN or te-IN for Hindi/Telugu
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setIsListening(true);
    };

    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      updateLocation(transcript);
      setIsListening(false);
    };

    recognition.onerror = (event) => {
      console.error("Speech recognition error", event.error);
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognition.start();
  };

  const handleOptimize = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setMandiFailures(null);

    try {
      const response = await fetch(`${API_BASE_URL}/api/optimize`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          location: location,
          budget: parseFloat(budget),
          land_acres: parseFloat(land),
          ...(soilTestRequired && {
            soil_test: {
              nitrogen: parseFloat(soilTest.nitrogen),
              phosphorus: parseFloat(soilTest.phosphorus),
              potassium: parseFloat(soilTest.potassium),
              ph: parseFloat(soilTest.ph)
            }
          })
        })
      });

      const data = await response.json();
      if (!response.ok) {
        const detail = data.detail;
        if (response.status === 428 && detail?.code === 'soil_test_required') {
          setSoilTestRequired(true);
          setSoilSummary(detail.soil_health_summary || null);
          setSoilSourceError(detail.soil_source_error || '');
        }
        if (detail?.mandi_prices) {
          setMandiFailures(detail.mandi_prices);
        }
        setError(typeof detail === 'string' ? detail : detail?.message || 'Could not generate a crop plan.');
        return;
      }

      setResult(data);
    } catch (error) {
      console.error('Failed to fetch crop plan', error);
      setError('Could not reach the crop-planning service. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-container">
      <div className="glass-panel main-panel">
        
        <div className="header">
          <h1>Zero-Friction <span>AgriOptimizer</span></h1>
          <p>We use public soil and market sources when compatible data are available, and clearly mark mock fallback values.</p>
        </div>

        {!result && (
          <form className="input-form" onSubmit={handleOptimize}>
            <div className="input-group">
              <label>📍 Your Village / Location</label>
              <div style={{ display: 'flex', gap: '10px' }}>
                <input 
                  type="text" 
                  placeholder="e.g., Nizamabad" 
                  value={location}
                  onChange={(e) => updateLocation(e.target.value)}
                  required
                  style={{ flex: 1 }}
                />
                <button 
                  type="button" 
                  className={`glow-button ${isListening ? 'listening' : ''}`}
                  onClick={handleVoiceInput}
                  style={{ padding: '0 20px', marginTop: 0 }}
                  title="Speak your location"
                >
                  {isListening ? '🎤...' : '🎤'}
                </button>
              </div>
            </div>
            
            <div className="input-group">
              <label>💰 Total Budget (₹)</label>
              <input 
                type="number" 
                placeholder="e.g., 50000" 
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                required
              />
            </div>

            <div className="input-group">
              <label>🌾 Total Land (Acres)</label>
              <input 
                type="number" 
                placeholder="e.g., 10" 
                value={land}
                onChange={(e) => setLand(e.target.value)}
                required
              />
            </div>

            {soilTestRequired && (
              <section className="soil-test-panel" aria-label="Soil test readings">
                <h3>Enter exact soil-test readings</h3>
                <p>
                  The currently connected backend requires measured values from the farmer&apos;s
                  soil test. Use this only when that backend explicitly requests these readings.
                </p>
                {soilSummary && (
                  <details className="soil-summary">
                    <summary>
                      Soil Health Card district summary ({soilSummary.district}, {soilSummary.cycle})
                    </summary>
                    <pre>{JSON.stringify(soilSummary.category_counts, null, 2)}</pre>
                  </details>
                )}
                {soilSourceError && (
                  <div className="error-banner" role="status">
                    Soil Health Card summary could not be retrieved: {soilSourceError}
                  </div>
                )}
                <div className="soil-test-grid">
                  {[
                    ['nitrogen', 'Nitrogen (N)'],
                    ['phosphorus', 'Phosphorus (P)'],
                    ['potassium', 'Potassium (K)'],
                    ['ph', 'pH']
                  ].map(([field, label]) => (
                    <div className="input-group" key={field}>
                      <label htmlFor={`soil-${field}`}>{label}</label>
                      <input
                        id={`soil-${field}`}
                        type="number"
                        min="0"
                        max={field === 'ph' ? '14' : undefined}
                        step="any"
                        value={soilTest[field]}
                        onChange={(event) => setSoilTest({ ...soilTest, [field]: event.target.value })}
                        required
                      />
                    </div>
                  ))}
                </div>
              </section>
            )}

            {error && <div className="error-banner" role="alert">{error}</div>}
            {mandiFailures && (
              <div className="error-banner" role="status">
                <strong>Recent mandi price check:</strong>
                <ul>
                  {Object.entries(mandiFailures).map(([crop, detail]) => (
                    <li key={crop}>{crop}: {detail.reason}</li>
                  ))}
                </ul>
              </div>
            )}

            <button type="submit" className="glow-button" disabled={loading}>
              {loading ? <span className="spinner"></span> : soilTestRequired ? "Use Soil Test and Optimize" : "Optimize My Farm"}
            </button>
          </form>
        )}

        {loading && (
          <div className="loading-state">
            <div className="pulse-ring"></div>
            <p>🛰️ Fetching location and historical weather...</p>
            <p>🤖 Running crop, yield, and Mandi API/mock-price analysis...</p>
          </div>
        )}

        {result && !loading && (
          <div className="results-dashboard fade-in">
            <div className="success-banner">
              {result.message}
            </div>
            {result.plan_is_simulated && (
              <div className="simulation-warning" role="alert">
                SIMULATED DEMO:
                {result.soil_data_is_simulated && ' One or more soil inputs came from static mock JSON.'}
                {result.price_data_is_simulated && ' One or more market prices are mock or older than 14 days.'}
                {' '}This plan and its profit estimate are not a real farm or market forecast.
              </div>
            )}
            
            <div className="dashboard-grid">
              <div className="glass-card stat-card">
                <h3>{result.plan_is_simulated ? 'Simulated Profit Estimate' : 'Total Estimated Profit'}</h3>
                <div className="profit-value">₹{result.total_estimated_profit.toLocaleString()}</div>
              </div>
              
              <div className="glass-card auto-data-card">
                <h3>Data used for this plan</h3>
                <div className="data-badges">
                  <span>Location: {result.resolved_location}</span>
                  <span style={{color: '#fde047'}}>Temp: {result.regional_data.temperature}°C</span>
                  <span style={{color: '#60a5fa'}}>Rain: {result.regional_data.rainfall}mm</span>
                  {[
                    ['nitrogen', 'N'],
                    ['phosphorus', 'P'],
                    ['potassium', 'K'],
                    ['ph', 'pH']
                  ].map(([feature, label]) => (
                    <span key={feature}>
                      {label}: {result.regional_data[feature]} (
                      {result.soil_data_provenance?.[feature]?.status === 'mock'
                        ? 'MOCK'
                        : result.soil_data_provenance?.[feature]?.status === 'api_estimate'
                          ? 'SoilGrids estimate'
                          : 'farmer test'})
                    </span>
                  ))}
                </div>
                <p className="soil-source-note">{result.soil_source_details?.note}</p>
                {result.soil_source_details?.soilgrids?.api_error && (
                  <p className="soil-source-note">
                    SoilGrids request unavailable; fallback values are labelled MOCK.
                  </p>
                )}
                {result.soil_health_source_error && (
                  <p className="soil-source-note">
                    Soil Health Card district summary could not be retrieved.
                  </p>
                )}
                {result.soil_health_summary && (
                  <details className="soil-summary">
                    <summary>
                      Soil Health Card district categories ({result.soil_health_summary.district},
                      {' '}{result.soil_health_summary.cycle})
                    </summary>
                    <pre>{JSON.stringify(result.soil_health_summary.category_counts, null, 2)}</pre>
                  </details>
                )}
              </div>
            </div>

            <h3 className="section-title">Mandi prices (district/state API with mock fallback)</h3>
            <div className="mandi-price-list">
              {Object.entries(result.mandi_prices).map(([crop, price]) => (
                <div className="mandi-price-item" key={crop}>
                  <strong>{crop}</strong>
                  {price.status === 'live'
                    ? ` LIVE Mandi API${price.is_stale ? ' — STALE' : ''} ${price.scope === 'district_aggregate' ? `district aggregate for ${price.district} (${price.market_count} markets${price.markets?.length ? `: ${price.markets.join(', ')}` : ''})` : 'state average'}: ₹${price.price_per_quintal.toLocaleString()} / quintal (reported ${price.arrival_date})`
                    : price.status === 'mock'
                      ? ` MOCK / NOT A REAL QUOTE: ₹${price.price_per_quintal.toLocaleString()} / quintal — ${price.reason}`
                    : ` Not used — ${price.reason}`}
                </div>
              ))}
            </div>

            <h3 className="section-title">Optimized Land Allocation</h3>
            <div className="allocation-list">
              {result.allocations.map((item, idx) => (
                <div key={idx} className="allocation-item">
                  <div className="crop-name">{item.crop}</div>
                  <div className="progress-container">
                    <div className="progress-bar" style={{ width: `${(item.allocated_acres / land) * 100}%` }}></div>
                  </div>
                  <div className="allocation-stats">
                    <span><strong>{item.allocated_acres}</strong> Acres</span>
                    <span>Expected Yield: {item.expected_yield_tonnes} Tonnes</span>
                    <span className="profit-text">+₹{item.profit_est.toLocaleString()}</span>
                  </div>
                </div>
              ))}
            </div>
            
            <button className="reset-button" onClick={() => setResult(null)}>Plan Another Farm</button>
          </div>
        )}
        
      </div>
    </div>
  );
}

export default App;
