import "@douyinfe/semi-ui/react19-adapter";

import React from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import "@douyinfe/semi-ui/dist/css/semi.min.css";

import { WorkspaceApp } from "./App";
import { createHttpChatApi, extractAccessToken } from "./services/httpChatApi";

const root = document.getElementById("root");

if (!root) {
  throw new Error("root element not found");
}

// P1-2（review 修复）：access-token 入口 `#/{token}`（closure TASK-005/006 access 流）
// 与 HashRouter 冲突——token hash 会被当作路由（空白页）。先摘出 token 并清掉 hash，
// 再由 HashRouter 从 / 进入（重定向 /home）；路由 hash（#/home 等）不当作 token。
const accessToken = extractAccessToken(window.location.hash);
if (accessToken !== null) {
  history.replaceState(null, "", window.location.pathname);
}
createRoot(root).render(
  <React.StrictMode>
    <HashRouter>
      <WorkspaceApp api={createHttpChatApi(accessToken ?? "")} />
    </HashRouter>
  </React.StrictMode>
);
