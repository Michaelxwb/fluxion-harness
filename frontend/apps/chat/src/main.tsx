import "@douyinfe/semi-ui/react19-adapter";

import React from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import "@douyinfe/semi-ui/dist/css/semi.min.css";

import { WorkspaceApp } from "./App";
import {
  createHttpChatApi,
  extractAccessToken,
  loadStoredAccessToken,
  storeAccessToken,
} from "./services/httpChatApi";

const root = document.getElementById("root");

if (!root) {
  throw new Error("root element not found");
}

// P1-2（review 修复）：access-token 入口 `#/{token}`（closure TASK-005/006 access 流）
// 与 HashRouter 冲突——token hash 会被当作路由（空白页）。先摘出 token 并清掉 hash，
// 再由 HashRouter 从 / 进入单页对话框；路由 hash 不当作 token。
// token 持久化 localStorage：刷新后 hash 只剩路由，凭缓存恢复会话，免重复绑定。
// 失效时由 App 清缓存（见 accessError 处理），不死循环。
function loadAccessToken(): string {
  const fromHash = extractAccessToken(window.location.hash);
  if (fromHash !== null) {
    history.replaceState(null, "", window.location.pathname);
    storeAccessToken(fromHash);
    return fromHash;
  }
  return loadStoredAccessToken() ?? "";
}

const accessToken = loadAccessToken();
createRoot(root).render(
  <React.StrictMode>
    <HashRouter>
      <WorkspaceApp api={createHttpChatApi(accessToken ?? "")} />
    </HashRouter>
  </React.StrictMode>
);
