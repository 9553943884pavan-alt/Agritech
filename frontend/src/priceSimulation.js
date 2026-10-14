export function hasSimulatedPrice(prices = {}) {
  return Object.values(prices || {}).some((price) =>
    price?.is_simulated === true
    || price?.proxy === true
    || price?.source === 'mock'
    || price?.status === 'mock'
    || price?.is_stale === true
  );
}
