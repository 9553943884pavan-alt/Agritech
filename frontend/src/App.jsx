import { lazy, Suspense, useState } from 'react';
import { hasSimulatedPrice } from './priceSimulation.js';

const ResultsDashboard = lazy(() => import('./ResultsDashboard.jsx'));

const API_BASE_URL = import.meta.env.VITE_API_URL || 'https://agritech-1euc.onrender.com';

function errorMessageFromResponse(detail) {
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const location = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : '';
      return location ? `${location}: ${item.msg}` : item.msg;
    }).filter(Boolean).join('; ');
  }
  return 'Could not generate a crop plan.';
}

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
  const priceDetails = {
    ...(result?.price_metadata || {}),
    ...(result?.mandi_prices || {})
  };
  const priceDataIsSimulated = Boolean(
    result?.price_data_is_simulated || hasSimulatedPrice(priceDetails)
  );

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
          location: location.trim(),
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

      let data;
      try {
        data = await response.json();
      } catch {
        setError(`The crop-planning service returned an unreadable response (HTTP ${response.status}).`);
        return;
      }
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
        setError(errorMessageFromResponse(detail));
        return;
      }

      if (
        !data
        || !Array.isArray(data.allocations)
        || !Number.isFinite(data.total_estimated_profit)
      ) {
        setError('The crop-planning service response is missing optimizer results. Please try again.');
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
                  minLength={2}
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
                min="0.01"
                step="any"
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
                min="0.01"
                step="any"
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
                        min={field === 'ph' ? '0.01' : '0'}
                        max={field === 'potassium' ? '500' : field === 'ph' ? '14' : '300'}
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
          <Suspense fallback={<div className="dashboard-loading" aria-busy="true" />}>
            <ResultsDashboard
              result={result}
              priceDetails={priceDetails}
              priceDataIsSimulated={priceDataIsSimulated}
              landInput={land}
              budgetInput={budget}
              onReset={() => setResult(null)}
            />
          </Suspense>
        )}
        
      </div>
    </div>
  );
}

export default App;
