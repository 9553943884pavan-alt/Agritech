import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { hasSimulatedPrice } from './priceSimulation.js';

const ResultsDashboard = lazy(() => import('./ResultsDashboard.jsx'));

const API_BASE_URL = import.meta.env.VITE_API_URL
  || (import.meta.env.DEV ? 'http://127.0.0.1:8000' : 'https://agritech-1euc.onrender.com');

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
  const [showManualSoil, setShowManualSoil] = useState(false);
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
  const prefetchControllerRef = useRef(null);
  const submitControllerRef = useRef(null);
  const priceDetails = {
    ...(result?.price_metadata || {}),
    ...(result?.mandi_prices || {})
  };
  const priceDataIsSimulated = Boolean(
    result?.price_data_is_simulated || hasSimulatedPrice(priceDetails)
  );

  const updateLocation = (value) => {
    setLocation(value);
    prefetchControllerRef.current?.abort();
    setSoilTestRequired(false);
    setSoilSummary(null);
    setSoilSourceError('');
    setMandiFailures(null);
    setError('');
  };

  useEffect(() => {
    let origin;
    try {
      origin = new URL(API_BASE_URL, window.location.href).origin;
    } catch {
      return undefined;
    }

    if (origin === window.location.origin) return undefined;

    const preconnect = document.createElement('link');
    preconnect.rel = 'preconnect';
    preconnect.href = origin;
    preconnect.crossOrigin = 'anonymous';
    const dnsPrefetch = document.createElement('link');
    dnsPrefetch.rel = 'dns-prefetch';
    dnsPrefetch.href = `//${new URL(origin).host}`;
    document.head.append(preconnect, dnsPrefetch);
    return () => {
      preconnect.remove();
      dnsPrefetch.remove();
    };
  }, []);

  useEffect(() => {
    const trimmedLocation = location.trim();
    if (trimmedLocation.length < 2 || result) return undefined;

    const controller = new AbortController();
    prefetchControllerRef.current?.abort();
    prefetchControllerRef.current = controller;
    const timeoutId = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/prefetch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ location: trimmedLocation }),
          signal: controller.signal,
        });
        if (!response.ok) {
          console.info(`Location prefetch was unavailable (HTTP ${response.status}); submit will fetch data.`);
        }
      } catch (prefetchError) {
        if (prefetchError.name !== 'AbortError') {
          console.info('Location prefetch was unavailable; submit will fetch data.', prefetchError);
        }
      }
    }, 300);

    return () => {
      window.clearTimeout(timeoutId);
      if (prefetchControllerRef.current === controller) {
        prefetchControllerRef.current = null;
      }
      controller.abort();
    };
  }, [location, result]);

  useEffect(() => () => {
    prefetchControllerRef.current?.abort();
    submitControllerRef.current?.abort();
  }, []);

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
    submitControllerRef.current?.abort();
    prefetchControllerRef.current?.abort();
    const controller = new AbortController();
    submitControllerRef.current = controller;
    setLoading(true);
    setError('');
    setMandiFailures(null);

    const hasValidSoilTest = Boolean(
      soilTest.nitrogen !== ''
      && soilTest.phosphorus !== ''
      && soilTest.potassium !== ''
      && soilTest.ph !== ''
      && Number.isFinite(parseFloat(soilTest.nitrogen))
      && Number.isFinite(parseFloat(soilTest.phosphorus))
      && Number.isFinite(parseFloat(soilTest.potassium))
      && Number.isFinite(parseFloat(soilTest.ph))
    );

    try {
      const response = await fetch(`${API_BASE_URL}/api/optimize`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        signal: controller.signal,
        body: JSON.stringify({
          location: location.trim(),
          budget: parseFloat(budget),
          land_acres: parseFloat(land),
          ...(((soilTestRequired || showManualSoil) && hasValidSoilTest) && {
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
      if (error.name !== 'AbortError') {
        console.error('Failed to fetch crop plan', error);
        setError('Could not reach the crop-planning service. Please try again.');
      }
    } finally {
      if (submitControllerRef.current === controller) {
        submitControllerRef.current = null;
        setLoading(false);
      }
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

            <div className="soil-test-toggle-container">
              <button
                type="button"
                className="soil-toggle-button"
                onClick={() => setShowManualSoil((prev) => !prev)}
                aria-expanded={showManualSoil || soilTestRequired}
              >
                <span className="toggle-left">
                  <span>🧪</span>
                  <span>
                    {showManualSoil || soilTestRequired
                      ? 'Hide Manual Soil Readings'
                      : 'Have Soil Health Card / Lab Test? Enter Manual Readings (Optional)'}
                  </span>
                </span>
                <span>{showManualSoil || soilTestRequired ? '▲' : '▼'}</span>
              </button>
              {showManualSoil && !soilTestRequired && (
                <p className="soil-toggle-hint">
                  Custom lab readings override default regional estimates. Leave empty or close to use automated estimates.
                </p>
              )}
            </div>

            {(showManualSoil || soilTestRequired) && (
              <section className="soil-test-panel" aria-label="Soil test readings">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <h3 style={{ margin: 0 }}>
                    {soilTestRequired ? 'Enter exact soil-test readings' : 'Manual Soil Test Readings (Lab / SHC)'}
                  </h3>
                  {!soilTestRequired && (
                    <button
                      type="button"
                      className="reset-soil-btn"
                      onClick={() => setSoilTest({ nitrogen: '', phosphorus: '', potassium: '', ph: '' })}
                      title="Clear soil values"
                    >
                      Clear
                    </button>
                  )}
                </div>
                <p>
                  {soilTestRequired
                    ? "The currently connected backend requires measured values from the farmer's soil test."
                    : 'Provide your laboratory soil-test values (Nitrogen, Phosphorus, Potassium in kg/ha and pH) to override automated geospatial estimates.'}
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
                    ['nitrogen', 'Nitrogen (N) - kg/ha', 'e.g., 80', '300'],
                    ['phosphorus', 'Phosphorus (P) - kg/ha', 'e.g., 40', '300'],
                    ['potassium', 'Potassium (K) - kg/ha', 'e.g., 45', '500'],
                    ['ph', 'Soil pH (1-14)', 'e.g., 6.5', '14']
                  ].map(([field, label, placeholder, maxVal]) => (
                    <div className="input-group" key={field}>
                      <label htmlFor={`soil-${field}`}>{label}</label>
                      <input
                        id={`soil-${field}`}
                        type="number"
                        min={field === 'ph' ? '0.01' : '0'}
                        max={maxVal}
                        step="any"
                        placeholder={placeholder}
                        value={soilTest[field]}
                        onChange={(event) => setSoilTest({ ...soilTest, [field]: event.target.value })}
                        required={soilTestRequired}
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
          <div className="loading-state" role="status" aria-live="polite" aria-busy="true">
            <div className="pulse-ring"></div>
            <p>Preparing location, soil, weather, crop, yield, and price data, then calculating your plan...</p>
            <div className="request-progress" role="progressbar" aria-label="Preparing your crop plan">
              <span />
            </div>
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
