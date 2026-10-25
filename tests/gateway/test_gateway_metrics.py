"""IM Gateway 指标目录：**无流量时 `GET /metrics` 也暴露完整目录** + label 卫生。

harness-arch 的「指标暴露与 label 卫生」把这一条写成硬约束，并点名了本单元的现状差异：
Gateway 此前只有 `install_metrics(app)`、零 `declare_metric` ⇒ 目录为空，监控侧看到的是
「服务活着但一个指标都没有」。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from muad_im_connector.main import app as connector_app
from muad_im_connector.metrics import CATALOG as CONNECTOR_CATALOG
from muad_im_gateway.main import app
from muad_im_gateway.metrics import CATALOG

#: label 里出现这些词就说明把资源身份/内容/凭据塞进了维度（无界基数 + 敏感）。
FORBIDDEN_LABEL_MARKERS = ("id", "secret", "token", "message", "user", "conversation", "run")


def test_gateway_metrics_catalog_is_visible_without_any_traffic() -> None:
    text = TestClient(app).get("/metrics").text
    assert CATALOG, "目录为空：等于没有声明"
    for name, _kind, _labels, _help in CATALOG:
        assert f"# HELP {name} " in text, f"{name} 未出现在 /metrics 目录里"


def test_gateway_metric_labels_carry_no_identity_or_content() -> None:
    for name, _kind, labels, _help in CATALOG:
        for label in labels:
            assert not any(marker in label for marker in FORBIDDEN_LABEL_MARKERS), (
                f"{name} 的 label {label!r} 命中禁止标记：label 只放低基数、非敏感维度"
            )


def test_connector_metrics_catalog_is_visible_without_any_traffic() -> None:
    """Connector 也是运行时角色：无流量时 `/metrics` 同样暴露完整目录（harness-arch）。"""
    text = TestClient(connector_app).get("/metrics").text
    assert CONNECTOR_CATALOG, "Connector 目录为空：等于没有声明"
    for name, _kind, _labels, _help in CONNECTOR_CATALOG:
        assert f"# HELP {name} " in text, f"{name} 未出现在 Connector /metrics 目录里"


def test_connector_metric_labels_carry_no_identity_or_content() -> None:
    for name, _kind, labels, _help in CONNECTOR_CATALOG:
        for label in labels:
            assert not any(marker in label for marker in FORBIDDEN_LABEL_MARKERS), (
                f"{name} 的 label {label!r} 命中禁止标记：label 只放低基数、非敏感维度"
            )


def test_gateway_and_connector_catalogs_do_not_overlap() -> None:
    """Gateway 是请求面、Connector 是连接面：目录不重叠（各自声明自己的角色）。"""
    gateway_names = {name for name, _kind, _labels, _help in CATALOG}
    connector_names = {name for name, _kind, _labels, _help in CONNECTOR_CATALOG}
    assert not gateway_names & connector_names
