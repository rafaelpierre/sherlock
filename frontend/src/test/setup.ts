import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
  configurable: true,
  value: vi.fn(),
});
let uuidSequence = 0;
Object.defineProperty(globalThis, "crypto", {
  configurable: true,
  value: {
    randomUUID: vi.fn(() => `00000000-0000-4000-8000-${String(++uuidSequence).padStart(12, "0")}`),
  },
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
