import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

let prefersReducedMotion = false;

export function setReducedMotionPreference(value: boolean) {
  prefersReducedMotion = value;
}

function createMediaQueryList(query: string): MediaQueryList {
  const matches = query.includes("prefers-reduced-motion") && prefersReducedMotion;

  return {
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    addListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
    removeEventListener: vi.fn(),
    removeListener: vi.fn(),
  };
}

function installMatchMediaMock() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(createMediaQueryList),
    writable: true,
  });
}

function installScrollIntoViewMock() {
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
    writable: true,
  });
}

installMatchMediaMock();
installScrollIntoViewMock();

beforeEach(() => {
  prefersReducedMotion = false;
  installMatchMediaMock();
  installScrollIntoViewMock();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
