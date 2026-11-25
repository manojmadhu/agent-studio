import '@testing-library/jest-dom/vitest';
import { mockBackend } from '../api/mock/backend';

// jsdom lacks ResizeObserver (used by the canvas)
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver ??= RO;

// fast, deterministic mock backend in tests
mockBackend().latency = 0;
mockBackend().speed = 0.002;
