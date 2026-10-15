import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import ResultsDashboard from './ResultsDashboard.jsx';

afterEach(cleanup);

const makeResult = (count = 3, overrides = {}) => {
  const crops = ['rice', 'jute', 'maize', 'cotton', 'banana', 'chickpea', 'mango'];
  const allocations = crops.slice(0, count).map((crop, index) => ({
    crop,
    allocated_acres: index + 1,
    expected_yield_tonnes: (index + 1) * 1.2,
    profit_est: (index + 1) * 10000,
  }));
  return {
    result: {
      message: 'Plan created.',
      allocations,
      total_estimated_profit: allocations.reduce((sum, item) => sum + item.profit_est, 0),
      utilized_budget: 50000,
      resolved_location: 'Nizamabad',
      regional_data: {
        nitrogen: 80,
        phosphorus: 40,
        potassium: 45,
        ph: 6.5,
        rainfall: 900,
        temperature: 28,
      },
      ...overrides,
    },
    priceDetails: Object.fromEntries(
      allocations.map((item) => [item.crop, {
        source: 'ceda_cached',
        price_rs_per_quintal: 2400,
        as_of: '2025-10-30',
        label: 'Historical mandi price, Oct 2025 (Agmarknet via CEDA)',
      }]),
    ),
  };
};

const renderDashboard = (count = 3, overrides = {}) => {
  const props = makeResult(count, overrides);
  render(
    <ResultsDashboard
      {...props}
      priceDataIsSimulated={false}
      landInput="10"
      budgetInput="100000"
      onReset={() => {}}
    />,
  );
  return props;
};

describe('ResultsDashboard', () => {
  it.each([1, 2, 3, 7])('renders the charts with %i crops', (count) => {
    renderDashboard(count);
    expect(screen.getByLabelText(/^Your land:/)).toBeTruthy();
    expect(screen.getByText('Profit by crop')).toBeTruthy();
    expect(screen.getByText(/Rs\. 1,00,000/, { selector: '.results-input-reminder strong' })).toBeTruthy();

    fireEvent.click(screen.getByText('More details'));
    expect(screen.getByText('Expected crop yield')).toBeTruthy();
    expect(screen.getByText('Price per quintal')).toBeTruthy();
    expect(screen.getByText('Land share vs profit share')).toBeTruthy();
    if (count === 7) expect(screen.getAllByText('Other').length).toBeGreaterThan(0);
  });

  it('formats rupees with Indian digit grouping in the summary', () => {
    renderDashboard(3, { total_estimated_profit: 135000 });
    expect(screen.getByText(/Estimated profit: Rs\. 1,35,000/)).toBeTruthy();
  });

  it('displays geocoded location names without pronunciation diacritics', () => {
    renderDashboard(1, {
      resolved_location: 'Nizāmābād, Nizamabad District, Telangana',
      message: 'Plan generated for Nizāmābād, Nizamabad District, Telangana.',
    });
    expect(screen.getByText('Plan generated for Nizamabad, Telangana.')).toBeTruthy();
    expect(screen.getByText('Location: Nizamabad, Telangana')).toBeTruthy();
  });

  it('shows hatched bars for mock and proxy prices, but not real prices', () => {
    const { result, priceDetails } = makeResult(3);
    priceDetails.rice = { source: 'mock', price_rs_per_quintal: 2000 };
    priceDetails.jute = {
      source: 'ceda_cached',
      status: 'live',
      price_rs_per_quintal: 2100,
      proxy: true,
    };
    render(
      <ResultsDashboard
        result={result}
        priceDetails={{ ...priceDetails, maize: { source: 'tgk_live', price_rs_per_quintal: 2200 } }}
        priceDataIsSimulated
        landInput="10"
        budgetInput="100000"
        onReset={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('More details'));

    const ricePrice = screen.getByRole('button', { name: /Rice: Rs\. 2,000/ });
    const jutePrice = screen.getByRole('button', { name: /Jute: Rs\. 2,100/ });
    const maizePrice = screen.getByRole('button', { name: /Maize: Rs\. 2,200/ });
    expect(ricePrice.querySelector('.bar-fill-simulated')).toBeTruthy();
    expect(jutePrice.querySelector('.bar-fill-simulated')).toBeTruthy();
    expect(maizePrice.querySelector('.bar-fill-simulated')).toBeNull();
    expect(within(jutePrice).getByText('Proxy')).toBeTruthy();

    expect(screen.getByText('Historical mandi price, Oct 2025 (Agmarknet via CEDA)').textContent)
      .not.toMatch(/live/i);
  });

  it('does not render a price row when the response has no price', () => {
    const { result } = makeResult(1);
    render(
      <ResultsDashboard
        result={result}
        priceDetails={{}}
        priceDataIsSimulated={false}
        landInput="1"
        budgetInput="1000"
        onReset={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('More details'));
    const priceCard = screen.getByRole('region', { name: /Price per quintal/ });
    expect(within(priceCard).getByText('No crop price is available to chart.')).toBeTruthy();
    expect(within(priceCard).queryByRole('button', { name: /Rice: Rs\./ })).toBeNull();
  });

  it('keeps zero and negative profit crops visible', () => {
    const { result } = makeResult(2, {
      allocations: [
        { crop: 'rice', allocated_acres: 1, expected_yield_tonnes: 1, profit_est: 0 },
        { crop: 'jute', allocated_acres: 1, expected_yield_tonnes: 1, profit_est: -100 },
      ],
      total_estimated_profit: -100,
    });
    render(
      <ResultsDashboard
        result={result}
        priceDetails={{}}
        priceDataIsSimulated={false}
        landInput="2"
        budgetInput="1000"
        onReset={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: /Rice: Rs\. 0/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Jute: −Rs\. 100/ })).toBeTruthy();
    fireEvent.click(screen.getByText('More details'));
    expect(screen.getByText('Loss')).toBeTruthy();
  });

  it('supports chart table toggles and starts details collapsed', () => {
    renderDashboard(2);
    expect(document.querySelector('.more-details').open).toBe(false);
    fireEvent.click(screen.getAllByRole('button', { name: 'Show as table' })[0]);
    expect(screen.getByRole('table')).toBeTruthy();
    expect(screen.getByText('Your land', { selector: 'caption' })).toBeTruthy();

    fireEvent.click(screen.getByText('More details'));
    const priceCard = screen.getByRole('region', { name: /Price per quintal/ });
    fireEvent.click(within(priceCard).getByRole('button', { name: 'Show as table' }));
    expect(within(priceCard).getByRole('table')).toBeTruthy();
  });

  it('handles older responses without optional data fields', () => {
    render(
      <ResultsDashboard
        result={{
          message: 'Plan created.',
          allocations: [{ crop: 'rice', allocated_acres: 1, profit_est: 1000 }],
          total_estimated_profit: 1000,
        }}
        priceDetails={{}}
        priceDataIsSimulated={false}
        landInput="1"
        budgetInput="1000"
        onReset={() => {}}
      />,
    );
    expect(screen.getByText(/did not return an AI explanation/)).toBeTruthy();
    fireEvent.click(screen.getByText('More details'));
    expect(screen.getByText('No crop price is available to chart.')).toBeTruthy();
    expect(screen.getAllByText(/Not built:/).length).toBeGreaterThan(0);
  });

  it('explains when the backend recommends crops that cannot be optimized', () => {
    renderDashboard(2, {
      top_crops_recommended: ['rice', 'jute', 'apple'],
      optimized_crops: ['rice', 'jute'],
    });
    const warning = screen.getAllByRole('status').find((item) => item.textContent.includes('Apple'));
    expect(warning?.textContent).toContain(
      'compatible yield or market-price data were unavailable',
    );
  });

  it('explains classifier rankings and the optimizer inputs and constraints', () => {
    renderDashboard(1, {
      classifier_reasoning: {
        method: 'random_forest_shap',
        crops: [{
          crop: 'rice',
          rank: 1,
          score: 0.72,
          feature_contributions: [
            { feature: 'N', value: 80, shap_value: 0.125 },
            { feature: 'rainfall', value: 900, shap_value: -0.032 },
          ],
        }],
      },
      optimizer_reasoning: {
        method: 'linear_program_highs',
        objective: 'maximize_net_profit',
        constraints: {
          land_acres_available: 2,
          land_acres_used: 1,
          land_limit_reached: false,
          budget_available: 100000,
          budget_used: 30000,
          budget_limit_reached: false,
          max_acres_per_crop: 2,
          crop_share_limit: 1,
        },
        crops: [{
          crop: 'rice',
          yield_tonnes_per_hectare: 2.5,
          yield_quintals_per_acre: 10.1,
          price_per_quintal: 2500,
          cost_per_acre: 10000,
          revenue_per_acre: 25250,
          profit_per_acre: 15250,
          allocated_acres: 1,
          profit_contribution: 15250,
        }],
      },
    });

    const reasoning = screen.getByText('Model and optimizer details');
    expect(reasoning.closest('details').open).toBe(false);
    fireEvent.click(reasoning);
    expect(screen.getByText('Why the classifier recommended these crops')).toBeTruthy();
    expect(screen.getByText((_, element) => (
      element.tagName === 'P' && element.textContent.includes('Model ranking score: 72%')
    ))).toBeTruthy();
    expect(screen.getByText(/Nitrogen/)).toBeTruthy();
    expect(screen.getByText('Why the optimizer chose this plan')).toBeTruthy();
    expect(screen.getAllByText('Linear programming (SciPy HiGHS)').length).toBeGreaterThan(0);
    expect(screen.getByText(/Land used: 1 \/ 2 acres/)).toBeTruthy();
    expect(screen.getByText(/Estimated net profit per acre/)).toBeTruthy();
    expect(screen.getByText(/not proof of cause/)).toBeTruthy();
  });

  it('renders the Gemini explanation immediately when the API returns it', () => {
    const result = makeResult(1).result;
    result.ai_reasoning = {
      status: 'ready',
      model: 'test-model',
      classifier_summary: 'The model ranked Rice first for the supplied measurements.',
      classifier_reasons: [{ crop: 'rice', reason: 'Nitrogen and rainfall have positive SHAP values.' }],
      optimizer_summary: 'Rice uses the available budget efficiently.',
      allocation_reasons: [{ crop: 'Rice', reason: 'Its estimated net margin is positive.' }],
    };
    render(
      <ResultsDashboard
        result={result}
        priceDetails={{}}
        priceDataIsSimulated={false}
        landInput="1"
        budgetInput="1000"
        onReset={() => {}}
      />,
    );
    expect(screen.getByText('AI-generated explanation (Gemini)')).toBeTruthy();
    expect(screen.getByText(/Nitrogen and rainfall have positive SHAP values/)).toBeTruthy();
    expect(screen.getByText(/Its estimated net margin is positive/)).toBeTruthy();
    expect(screen.getByText(/AI-written summary based on the figures below/)).toBeTruthy();
  });

  it('explains when the Gemini API key has not been configured', () => {
    const result = makeResult(1).result;
    result.ai_reasoning = { status: 'not_configured', error_code: 'missing_api_key' };
    render(
      <ResultsDashboard
        result={result}
        priceDetails={{}}
        priceDataIsSimulated={false}
        landInput="1"
        budgetInput="1000"
        onReset={() => {}}
      />,
    );
    expect(screen.getByRole('status').textContent).toContain('GEMINI_API_KEY');
  });
});
