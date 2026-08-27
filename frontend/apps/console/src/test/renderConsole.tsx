import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConsoleApp } from "../App";
import { createInMemoryConsoleApi, type ConsoleSeed } from "../services/inMemoryConsoleApi";
import type { ConsoleView } from "../types/navigation";

interface RenderConsoleOptions {
  readonly initialView?: ConsoleView;
  readonly seed?: ConsoleSeed;
  readonly initialAgentId?: string;
}

export function renderConsole(options: RenderConsoleOptions = {}) {
  const api = createInMemoryConsoleApi(options.seed);
  const user = userEvent.setup();
  const view = render(
    <ConsoleApp
      api={api}
      initialView={options.initialView ?? "resources"}
      initialAgentId={options.initialAgentId}
    />
  );

  return { ...view, api, user };
}
