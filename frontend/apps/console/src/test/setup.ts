import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest 未开启 `globals: true`，@testing-library/react 的自动 cleanup 依赖全局
// `afterEach` 判断、不会注册（与 apps/chat 的 setup 保持一致，需手动注册）。
// 否则测试结束后的 pending Scheduler 任务（setImmediate 宏任务）会在 jsdom
// 环境拆除后才触发，报 `ReferenceError: window is not defined`。
afterEach(cleanup);

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

// Semi Design 的 Typography ellipsis 检测依赖 Range.getBoundingClientRect，
// jsdom 未实现该 API，这里提供一个只读返回 0 尺寸的桩，避免测试环境报 unhandled rejection。
if (typeof Range !== "undefined") {
  Range.prototype.getBoundingClientRect = () =>
    ({
      width: 0,
      height: 0,
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      x: 0,
      y: 0,
      toJSON: () => ({})
    }) as DOMRect;
}
