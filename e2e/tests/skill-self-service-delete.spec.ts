import { test } from '@playwright/test';

// 计划阶段先落可收集骨架；TASK-016 补真实登录、详情、解绑、二次确认步骤与断言。
test.skip('S-40 管理员在 Console 解绑并软删已审批 Skill', async () => {
  // TASK-016: 打开 /skills，进入详情，查看 Agent 解绑列表，二次确认并刷新。
});
