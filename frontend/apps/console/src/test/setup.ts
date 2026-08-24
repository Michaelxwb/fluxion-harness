import "@testing-library/jest-dom/vitest";

class TestResizeObserver {
  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}
}

Object.defineProperty(window, "ResizeObserver", {
  configurable: true,
  value: TestResizeObserver
});

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: (query: string) => ({
    addEventListener: () => undefined,
    addListener: () => undefined,
    dispatchEvent: () => false,
    matches: false,
    media: query,
    onchange: null,
    removeEventListener: () => undefined,
    removeListener: () => undefined
  })
});

const canvasContext: Partial<CanvasRenderingContext2D> = {
  clearRect: () => undefined,
  fillRect: () => undefined,
  fillStyle: "rgba(0,0,0,0)"
};

Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  configurable: true,
  value: () => canvasContext
});
