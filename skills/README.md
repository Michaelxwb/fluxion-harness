# skills

手动平台验证用的 Skill 包（不属于产品代码，不参与验收）。

## 可直接导入的 zip 包

zip 是**构建产物**，不进版本库：先跑 `./skills/package.sh`（无参打包全部，或传目录名），产物在 `skills/*.zip`，再用 Console「Skill 管理 → 导入」上传；包根就是 `SKILL.md` + `muad.skill.json` + `scripts/`（平铺包）。

| zip | 用途 |
|---|---|
| `self-skill-creator.zip` | 指导用户写自建 Skill（草稿通过 `skill_draft` 工具写入平台） |
| `self-skill-review.zip` | 只读确定性审查（`scripts/check.py` + 规则参考） |
| `self-skill-upload.zip` | 把草稿提交为 PENDING artifact（`skill_draft` 的 review/submit action） |
| `smoke-platform-sample.zip` | `smoke_platform` 验证样例（健康检查、CSRF 业务接口、Traceid） |

打包脚本：

```bash
./skills/package.sh                       # 默认打包 skills/ 下所有 Skill（排除 _templates/）
./skills/package.sh smoke-platform-sample # 只打包指定 Skill，可传多个
```

只收 Skill 必需文件，排除 `__MACOSX`、`.DS_Store`、`__pycache__`、`*.pyc`、
`.pytest_cache`/`.mypy_cache`/`.ruff_cache`、`.venv`、`node_modules`；文件时间固定为 1980-01-01，
保证同一份内容打出相同字节。打包后用 Console 的 `validated_package` 校验每个 zip。

导入时不再手填版本 / Skill key / 默认脚本：版本取 `muad.skill.json` 的 `version`（缺省 `1.0.0`），
Skill key 由 `SKILL.md` 的 `name` 生成，默认脚本取 manifest 的 `entrypoint`。更新已导入的 Skill 时，
先把 manifest 的 `version` 递增，否则会因版本冲突（`SKILL_VERSION_EXISTS`）被拒。

## 目录

| 目录 | 用途 |
|---|---|
| `_templates/business-skill-python` | 新平台业务 Skill 模板（session-manager + requests + `SKILL_OUTPUT_DIR`） |
| `smoke-platform-sample` | 上表 zip 的源目录；本地端到端见 `tools/fake-business-platform/smoke_check.py` |
| `self-skill-creator` / `self-skill-review` / `self-skill-upload` | 自建 Skill 三件套源目录 |

## 自建 Skill 三件套说明

- 草稿不再写本地 `skill-staging/`，改为平台草稿（按 tenant + user + agent 隔离、revision CAS）。
- 上传不再调用旧项目的 `/internal/v1/skills/private/ingest`，改用 Runtime 的 `skill_draft` 工具
  （action：create/get/read/write/delete/review/status/submit）。
- `muad-progress` 进度通知在本平台没有对应实现，相关说明已移除。
- `self-skill-review` 的自测夹具放在 `tests/skills/self_skill_review/`（夹具含平台不允许的
  文件类型，放进 Skill 包会导致导入校验失败）。
