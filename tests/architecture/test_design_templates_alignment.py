from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs"


FULL_REQUIRED = [
    "## 1. 文档控制",
    "## 2. 需求分析",
    "### 2.1 需求概述",
    "### 2.2 痛点与价值",
    "### 2.3 功能方案",
    "### 2.4 范围与边界",
    "### 2.5 验收条件",
    "## 3. 技术设计",
    "### 3.1 方案选型",
    "### 3.2 架构设计",
    "### 3.3 数据设计",
    "### 3.4 接口设计",
    "### 3.5 质量实现方案",
    "## 4. 部署与运维",
    "## 5. 风险与依赖",
    "## 6. 需求追溯矩阵",
    "## Spec Compliance Matrix",
]

LITE_REQUIRED = [
    "## 1. 文档控制",
    "## 2. 需求分析",
    "### 2.1 需求概述",
    "### 2.2 功能方案",
    "### 2.3 范围与边界",
    "### 2.4 验收条件",
    "## 3. 技术设计",
    "### 3.1 技术选型",
    "### 3.2 架构设计",
    "### 3.3 接口设计",
    "### 3.4 性能与容量考量",
    "## 4. 风险与依赖",
    "## Spec Compliance Matrix",
]

FRONTEND_REQUIRED = [
    "## 1. 文档控制",
    "## 2. 需求分析",
    "### 2.1 需求概述",
    "### 2.2 功能方案",
    "### 2.3 范围与边界",
    "### 2.4 验收条件",
    "## 3. 前端技术设计",
    "### 3.1 技术选型",
    "### 3.2 页面与路由结构",
    "### 3.3 组件设计",
    "### 3.4 组件接口契约",
    "### 3.5 状态与数据流",
    "### 3.6 UI 状态",
    "### 3.7",
    "## 4. 风险与依赖",
    "## Spec Compliance Matrix",
]


def _assert_sections(path: Path, required: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [heading for heading in required if heading not in text]
    assert not missing, f"{path.relative_to(ROOT)} missing template sections: {missing}"
    assert "{模块名称}" not in text
    assert "{{" not in text


def test_full_design_documents_follow_full_template() -> None:
    module_dir = DOCS / "02-模块设计"
    full_docs = sorted(module_dir.glob("*/design-full.md"))
    assert len(full_docs) == 18, "V1.11: 19 modules, 18 full + 1 lite"
    for path in full_docs:
        _assert_sections(path, FULL_REQUIRED)


def test_lite_design_documents_follow_lite_template() -> None:
    module_dir = DOCS / "02-模块设计"
    lite_docs = sorted(module_dir.glob("*/design-lite.md"))
    assert len(lite_docs) == 1, "V1.11: only 16-Knowledge规划 is lite"
    for path in lite_docs:
        _assert_sections(path, LITE_REQUIRED)


def test_console_design_follows_frontend_template() -> None:
    frontend_dir = DOCS / "03-前端设计"
    frontend_docs = sorted(frontend_dir.glob("*/design-frontend.md"))
    assert len(frontend_docs) >= 11, "V1.12: 00-11 frontend modules"
    for path in frontend_docs:
        _assert_sections(path, FRONTEND_REQUIRED)


def test_all_database_module_designs_reference_common_columns() -> None:
    for path in sorted((DOCS / "02-模块设计").glob("*/design-*.md")):
        text = path.read_text(encoding="utf-8")
        if "#### 表" in text:
            assert "is_deleted" in text, path.name
            assert "create_time" in text, path.name
            assert "update_time" in text, path.name
