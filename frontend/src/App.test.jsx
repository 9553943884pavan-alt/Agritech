import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App.jsx';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function fillFarmInputs({ location = 'Nizamabad', budget = '125000.5', land = '5.25' } = {}) {
  fireEvent.change(screen.getByPlaceholderText('e.g., Nizamabad'), {
    target: { value: location },
  });
  fireEvent.change(screen.getByPlaceholderText('e.g., 50000'), {
    target: { value: budget },
  });
  fireEvent.change(screen.getByPlaceholderText('e.g., 10'), {
    target: { value: land },
  });
}

describe('frontend optimizer integration', () => {
  it('posts farmer inputs and renders the optimizer response', async () => {
    const optimizerResponse = {
      message: 'Soil inputs and prices are estimates.',
      resolved_location: 'Nizamabad',
      plan_is_simulated: false,
      soil_data_is_simulated: false,
      price_data_is_simulated: false,
      total_estimated_profit: 135000,
      ai_reasoning: {
        status: 'ready',
        model: 'gemini-3.1-flash-lite',
        classifier_summary: 'The classifier ranked crops using the supplied soil and weather features.',
        classifier_reasons: [{ crop: 'rice', reason: 'Rainfall and nitrogen influenced the model score.' }],
        optimizer_summary: 'The optimizer compared estimated profit under the land and budget limits.',
        allocation_reasons: [{ crop: 'Rice', reason: 'Rice was assigned land in the selected plan.' }],
      },
      utilized_budget: 87500,
      allocated_land_acres: 5,
      allocations: [
        {
          crop: 'rice',
          allocated_acres: 3,
          expected_yield_tonnes: 4.2,
          profit_est: 90000,
        },
      ],
      regional_data: {
        nitrogen: 80,
        phosphorus: 40,
        potassium: 45,
        ph: 6.5,
        temperature: 28,
        rainfall: 900,
      },
      mandi_prices: {
        rice: {
          status: 'cached',
          source: 'ceda_cached',
          price_rs_per_quintal: 2200,
          as_of: '2025-10-30',
          is_simulated: false,
        },
      },
      price_metadata: {
        rice: {
          source: 'ceda_cached',
          label: 'Historical mandi price, Oct 2025 (Agmarknet via CEDA)',
        },
      },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => optimizerResponse,
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fillFarmInputs();
    fireEvent.click(screen.getByRole('button', { name: 'Optimize My Farm' }));

    expect(await screen.findByText(/Plant Rice on 3 acres/)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/optimize$/);
    if (!import.meta.env.VITE_API_URL) {
      expect(url).toBe('http://127.0.0.1:8000/api/optimize');
    }
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({
      location: 'Nizamabad',
      budget: 125000.5,
      land_acres: 5.25,
    });
    expect(screen.getByText('Your entered land:').parentElement.textContent).toContain('5.3 acres');
    expect(screen.getByText('Your entered budget:').parentElement.textContent).toContain('Rs. 1,25,001');
    expect(screen.getByText('Rs. 90,000')).toBeTruthy();
    expect(screen.getByText('AI-generated explanation (Gemini)')).toBeTruthy();
    expect(screen.getByText('Rainfall and nitrogen influenced the model score.')).toBeTruthy();
  });

  it('surfaces FastAPI validation detail and keeps the form available', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [{ loc: ['body', 'soil_test', 'ph'], msg: 'Input should be greater than 0' }],
      }),
    }));
    render(<App />);
    fillFarmInputs();
    fireEvent.click(screen.getByRole('button', { name: 'Optimize My Farm' }));

    expect((await screen.findByRole('alert')).textContent).toContain(
      'soil_test.ph: Input should be greater than 0',
    );
    expect(screen.getByPlaceholderText('e.g., Nizamabad')).toBeTruthy();
  });

  it('rejects a successful response with no optimizer payload', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ message: 'not an optimizer result' }),
    }));
    render(<App />);
    fillFarmInputs();
    fireEvent.click(screen.getByRole('button', { name: 'Optimize My Farm' }));

    expect((await screen.findByRole('alert')).textContent).toContain(
      'The crop-planning service response is missing optimizer results.',
    );
    expect(screen.getByPlaceholderText('e.g., Nizamabad')).toBeTruthy();
  });

  it('continues to the single optimize request when prefetch fails', async () => {
    const optimizerResponse = {
      message: 'Soil inputs and prices are estimates.',
      resolved_location: 'Nizamabad',
      total_estimated_profit: 1000,
      ai_reasoning: { status: 'not_configured' },
      utilized_budget: 1000,
      allocated_land_acres: 1,
      allocations: [
        {
          crop: 'rice',
          allocated_acres: 1,
          expected_yield_tonnes: 1,
          profit_est: 1000,
        },
      ],
      regional_data: { temperature: 28, rainfall: 900 },
    };
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('prefetch unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => optimizerResponse,
      });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    fireEvent.change(screen.getByPlaceholderText('e.g., Nizamabad'), {
      target: { value: 'Nizamabad' },
    });
    await new Promise((resolve) => window.setTimeout(resolve, 350));

    fireEvent.change(screen.getByPlaceholderText('e.g., 50000'), {
      target: { value: '50000' },
    });
    fireEvent.change(screen.getByPlaceholderText('e.g., 10'), {
      target: { value: '2' },
    });
    fireEvent.submit(screen.getByRole('button', { name: 'Optimize My Farm' }).closest('form'));

    expect(await screen.findByRole('button', { name: 'Plan Another Farm' })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/prefetch$/);
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/api\/optimize$/);
  });

  it('allows the user to toggle manual soil test inputs and sends custom N, P, K, pH readings', async () => {
    const optimizerResponse = {
      message: 'Custom soil inputs were used.',
      resolved_location: 'Guntur',
      total_estimated_profit: 50000,
      ai_reasoning: { status: 'ready' },
      utilized_budget: 40000,
      allocated_land_acres: 2,
      allocations: [
        {
          crop: 'chickpea',
          allocated_acres: 2,
          expected_yield_tonnes: 1.5,
          profit_est: 50000,
        },
      ],
      regional_data: {
        nitrogen: 95,
        phosphorus: 45,
        potassium: 50,
        ph: 7.2,
        temperature: 30,
        rainfall: 250,
      },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => optimizerResponse,
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fillFarmInputs({ location: 'Guntur', budget: '90000', land: '4' });

    // Click the toggle button to reveal manual soil readings
    const toggleBtn = screen.getByRole('button', {
      name: /Have Soil Health Card \/ Lab Test\? Enter Manual Readings/i,
    });
    fireEvent.click(toggleBtn);

    // Expect the inputs to appear
    expect(screen.getByLabelText(/Nitrogen \(N\)/i)).toBeTruthy();
    expect(screen.getByLabelText(/Phosphorus \(P\)/i)).toBeTruthy();
    expect(screen.getByLabelText(/Potassium \(K\)/i)).toBeTruthy();
    expect(screen.getByLabelText(/Soil pH/i)).toBeTruthy();

    // Fill the soil inputs
    fireEvent.change(screen.getByLabelText(/Nitrogen \(N\)/i), { target: { value: '95' } });
    fireEvent.change(screen.getByLabelText(/Phosphorus \(P\)/i), { target: { value: '45' } });
    fireEvent.change(screen.getByLabelText(/Potassium \(K\)/i), { target: { value: '50' } });
    fireEvent.change(screen.getByLabelText(/Soil pH/i), { target: { value: '7.2' } });

    // Submit the form
    fireEvent.click(screen.getByRole('button', { name: 'Optimize My Farm' }));

    expect(await screen.findByRole('button', { name: 'Plan Another Farm' })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0];
    const body = JSON.parse(options.body);
    expect(body.soil_test).toEqual({
      nitrogen: 95,
      phosphorus: 45,
      potassium: 50,
      ph: 7.2,
    });
  });
});

