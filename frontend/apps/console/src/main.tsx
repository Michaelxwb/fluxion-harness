import "@douyinfe/semi-ui/react19-adapter";

import React from "react";
import { createRoot } from "react-dom/client";
import "@douyinfe/semi-ui/dist/css/semi.min.css";

import { ConsoleApp } from "./App";
import { createHttpConsoleApi } from "./services/httpConsoleApi";

const root = document.getElementById("root");

if (!root) {
  throw new Error("root element not found");
}

createRoot(root).render(
  <React.StrictMode>
    <ConsoleApp api={createHttpConsoleApi()} />
  </React.StrictMode>
);
