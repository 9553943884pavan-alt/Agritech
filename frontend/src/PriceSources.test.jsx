import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import {
  PriceSimulationBanner,
  PriceSources
} from './PriceSources.jsx';
import { hasSimulatedPrice } from './priceSimulation.js';

afterEach(cleanup);

describe('PriceSources', () => {
  it('renders source badges for TGK, CEDA, mock, and proxy prices', () => {
    render(
      <PriceSources
        prices={{
          rice: {
            source: 'tgk_live',
            price_rs_per_quintal: 4200,
            as_of: '2026-10-13'
          },
          maize: {
            source: 'ceda_cached',
            price_rs_per_quintal: 2250,
            as_of: '2025-10-30',
            label: 'Historical mandi price, Oct 2025 (Agmarknet via CEDA)'
          },
          apple: {
            source: 'mock',
            price_rs_per_quintal: 7000,
            is_simulated: true
          },
          mothbeans: {
            source: 'ceda_cached',
            price_rs_per_quintal: 4800,
            proxy: true,
            warning: 'Moth bean dal price used as a proxy.'
          }
        }}
      />
    );

    expect(screen.getByText('Live (TGK)')).toBeTruthy();
    expect(screen.getByText('Historical mandi price, Oct 2025 (Agmarknet via CEDA)')).toBeTruthy();
    expect(screen.getByText('Mock (simulated)')).toBeTruthy();
    expect(screen.getByText('Proxy price')).toBeTruthy();
    expect(screen.getByText('Moth bean dal price used as a proxy.')).toBeTruthy();
    expect(screen.getByText('Proxy price').getAttribute('title')).toBe(
      'Moth bean dal price used as a proxy'
    );
    expect(screen.getByText('Historical mandi price, Oct 2025 (Agmarknet via CEDA)').textContent)
      .not.toMatch(/live/i);
  });

  it('uses legacy API fields without throwing', () => {
    render(
      <PriceSources
        prices={{
          rice: { status: 'live', price_per_quintal: 3200, arrival_date: '2026-10-13' },
          maize: { status: 'mock', price_per_quintal: 2200, reason: 'No quote.' }
        }}
      />
    );

    expect(screen.getByText('Live (TGK)')).toBeTruthy();
    expect(screen.getByText('Mock (simulated)')).toBeTruthy();
    expect(screen.getByText('No quote.')).toBeTruthy();
  });

  it('shows the simulation banner for mock or proxy inputs, but not verified prices', () => {
    const { rerender } = render(
      <PriceSimulationBanner
        prices={{ maize: { source: 'ceda_cached', is_simulated: false } }}
      />
    );
    expect(screen.queryByRole('alert')).toBeNull();

    rerender(
      <PriceSimulationBanner
        prices={{ maize: { source: 'mock', is_simulated: true } }}
      />
    );
    expect(screen.getByRole('alert')).toBeTruthy();

    rerender(
      <PriceSimulationBanner
        prices={{ mothbeans: { source: 'ceda_cached', proxy: true } }}
      />
    );
    expect(screen.getByRole('alert').textContent).toMatch(/proxy prices/);
  });

  it('identifies legacy mock records as simulated', () => {
    expect(hasSimulatedPrice({ maize: { status: 'mock' } })).toBe(true);
    expect(hasSimulatedPrice({ maize: { source: 'ceda_cached' } })).toBe(false);
  });
});
