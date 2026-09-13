"""Cross-owner checks for the seven contract gaps found in the design review."""

import re

import pytest

from tests.design_contracts import AGENT_DOC, CAPABILITY_DOC, CHANNEL_DOC, DOCS, contract_text, scenario_rows


def _section(path: str, heading: str) -> str:
    source = (DOCS / path).read_text()
    start = source.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    end = re.search(rf"^#{{1,{level}}} ", source[start + len(heading):], re.M)
    return source[start:] if end is None else source[start:start + len(heading) + end.start()]


def test_capability_test_candidates_do_not_require_a_service() -> None:
    section = _section("03-前端设计/90-Console交互规格.md", "## 4.7")
    assert "/api/v1/capabilities/{capability_id}/test-user-candidates" in section
    assert "CAP-API-07" in section
    assert "service_id" not in section and "SVC-API-11" not in section


def test_both_test_entrypoints_use_the_same_user_policy() -> None:
    service = _section("02-模块设计/05-Service与Execution/design-full.md", "#### SVC-API-11:")
    capability = _section("02-模块设计/07-Capability-Runtime/design-full.md", "#### CAP-API-07:")
    for section in (service, capability):
        assert "USR-LIB-02" in section
        assert "END_USER" in section
        assert "TEST_USER_ACCESS_INVALID" in section


@pytest.mark.parametrize("document,heading", [
    ("02-模块设计/05-Service与Execution/design-full.md", "#### SVC-API-06:"),
    (CAPABILITY_DOC, "#### CAP-API-05:"),
])
def test_submission_rechecks_the_test_identity(document: str, heading: str) -> None:
    assert "USR-LIB-02.require_user" in _section(document, heading)


def test_async_only_capability_cannot_be_advertised_as_direct_invocation() -> None:
    predicate = contract_text(CAPABILITY_DOC, "direct-invocation-predicate", "text")
    assert "AND 'SYNC' IN supported_execution_modes" in predicate


@pytest.mark.parametrize("heading", ["#### PLAT-API-02:", "#### PLAT-API-04:"])
def test_platform_write_contracts_match_the_admin_only_frontend(heading: str) -> None:
    section = _section("02-模块设计/09-Auth与项目平台/design-full.md", heading)
    assert "**仅 Admin**" in section and "ADMIN_REQUIRED" in section
    assert "FIELD_ADMIN_ONLY" not in section and "Builder（未携带敏感字段）" not in section


def test_acceptance_scenario_ids_are_unique_and_have_complete_rows() -> None:
    assert scenario_rows()


@pytest.mark.parametrize("document", [
    AGENT_DOC, CAPABILITY_DOC, "03-前端设计/00-Console公共框架/design-frontend.md",
])
def test_acceptance_tables_keep_their_declared_column_count(document: str) -> None:
    width: int | None = None
    for line in (DOCS / document).read_text().splitlines():
        cells = re.split(r"(?<!\\)\|", line)
        if line.startswith(("| 场景ID |", "| Spec/Rule |", "| ID | 类型 |")):
            width = len(cells)
        elif not line.startswith("|"):
            width = None
        elif width is not None:
            assert len(cells) == width, f"{document}: {line}"


def test_interface_index_and_readme_match_the_owner_contracts() -> None:
    owners: dict[str, str] = {}
    readme = (DOCS / "02-模块设计/README.md").read_text().split("## 3. Interface Owner", 1)[1]
    pattern = r"^\| ([A-Z]+-(?:API|LIB|INT|DATA|CLI)-\d+[A-Z]?) \|"
    for path in (DOCS / "02-模块设计").glob("*/design-full.md"):
        section = _section(str(path.relative_to(DOCS)), "#### 3.4.1 接口清单")
        identifiers = re.findall(pattern, section, re.M)
        assert identifiers
        row = next(line for line in readme.splitlines() if line.startswith(f"| {path.parent.name} |"))
        assert set(re.findall(r"`([^`]+)`", row)) == set(identifiers)
        for identifier in identifiers:
            assert identifier not in owners, f"Multiple owners for {identifier}"
            owners[identifier] = path.parent.name
    index = (DOCS / "01-架构与规范/09-接口所有权与详细设计索引.md").read_text()
    entries = re.findall(pattern + r" ([^|]+) \|", index, re.M)
    assert len(entries) == len(owners)
    assert dict(entries) == owners


def test_binding_inline_ui_has_the_same_replacement_semantics_as_owner() -> None:
    frontend = (DOCS / "03-前端设计/03-智能体管理/design-frontend.md").read_text()
    assert "提交该维度完整 ID 集合" in frontend
    assert "只含该行变化" not in frontend
    assert "DIRECT_BINDING" in frontend and "SKILL_DECLARED" in frontend


@pytest.mark.parametrize("field", ["path", "method", "shared_secret_ref", "supported_execution_modes"])
def test_capability_form_exposes_all_required_configuration(field: str) -> None:
    section = _section("03-前端设计/04-能力管理/design-frontend.md", "### 3.4")
    assert field in section


def test_execution_ui_consumes_the_actual_response_projection() -> None:
    section = _section("03-前端设计/10-执行记录/design-frontend.md", "### 3.4")
    assert "scope_refs" in section and "resource_scope_summary" in section
    assert "resource_scope.refs" not in section
    assert "即后端投递侧的 `RETRY_WAIT`" not in section
    assert "RETRY_PENDING" in section
    backend = _section("02-模块设计/05-Service与Execution/design-full.md", "#### EXE-API-02:")
    assert "CANCEL/RETRY/RESUME/REDELIVER" in backend


def test_permission_cas_has_all_replay_and_isolation_predicates() -> None:
    sql = contract_text(CHANNEL_DOC, "delivery-permit-consume-sql", "sql")
    for predicate in (
        "id = :delivery_id", "tenant_id = :tenant_id", "is_deleted = false",
        "status = 'SENDING'", "lease_epoch = :lease_epoch",
        "lease_expires_at > CURRENT_TIMESTAMP", "permit_consumed_at IS NULL",
    ):
        assert predicate in sql
    assert "SET permit_consumed_at = CURRENT_TIMESTAMP" in sql
    assert "RETURNING" in sql


@pytest.mark.parametrize(
    ("requirement", "assertion_terms"),
    [
        ("P04", ("revision", "新请求")),
        ("P12", ("授权", "AGENT_ACCESS_DENIED")),
        ("P14", ("checksum", "v1")),
        ("P15", ("platform_label", "分组")),
        ("P17", ("分页", "终止")),
        ("P18", ("summary", "artifact")),
        ("P19", ("manifest", "seed")),
        ("P20", ("菜单", "Channel", "Async")),
        ("P22", ("黄金旅程", "MSS", "WeCom")),
        ("P23", ("HUMAN", "RESUME", "超时")),
        ("P31", ("模板", "签发")),
        ("P33", ("Route", "许可")),
    ],
)
def test_traceability_links_relevant_assertions(
    requirement: str, assertion_terms: tuple[str, ...]
) -> None:
    matrix = (DOCS / "04-追溯与验收/01-总体设计到模块设计追溯矩阵.md").read_text()
    line = next(line for line in matrix.splitlines() if line.startswith(f"| {requirement} "))
    references = line.strip("|").split("|")[2].split(",")
    scenarios = scenario_rows()
    assertions = " ".join(scenarios[reference.strip()] for reference in references)
    for term in assertion_terms:
        assert term.lower() in assertions.lower(), f"{requirement} does not verify {term}"
