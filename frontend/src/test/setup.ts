import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
  configurable: true,
  value: vi.fn(),
});
Object.defineProperty(globalThis, "crypto", {
  configurable: true,
  value: { randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000000") },
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
