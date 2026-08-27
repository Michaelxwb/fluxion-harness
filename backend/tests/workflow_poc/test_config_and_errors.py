"""verifier 覆盖：配置优先级（backend-platform）、错误码集中、日志字段（backend-logging）。"""

from __future__ import annotations

import json
import logging

import pytest

from fluxion.config.workflow import (
    DEFAULT_CONFIG_FILE,
    DBOS_DATABASE_URL_ENV,
    RESTATE_URL_ENV,
    TEMPORAL_ADDRESS_ENV,
    WORKFLOW_CONFIG_FILE_ENV,
    WorkflowBackendSettings,
)
from fluxion.errors import workflow as workflow_errors
from fluxion.errors.workflow import WorkflowEngineError
from fluxion.observability.logging import WORKFLOW_LOGGER_NAME, emit_workflow_event_log


def test_backend_config_priority_env_over_file_over_default(tmp_path) -> None:
    """优先级：环境变量 > 配置文件 > 默认值（空环境变量值视为未设置）。"""
    config_file = tmp_path / DEFAULT_CONFIG_FILE
    config_file.write_text(
        json.dumps(
            {
                "temporal_address": "file:7233",
                "dbos_database_url": "file:postgresql://poc/dbos",
                "restate_url": "file:http://localhost:8080",
            }
        ),
        encoding="utf-8",
    )

    # 1) 全无 env → 取配置文件
    settings = WorkflowBackendSettings.resolve(environ={}, config_path=config_file)
    assert settings.temporal_address == "file:7233"
    assert settings.dbos_database_url == "file:postgresql://poc/dbos"
    assert settings.restate_url == "file:http://localhost:8080"

    # 2) env 覆盖 file
    settings = WorkflowBackendSettings.resolve(
        environ={TEMPORAL_ADDRESS_ENV: "env:7233", RESTATE_URL_ENV: "env:http://restate"},
        config_path=config_file,
    )
    assert settings.temporal_address == "env:7233"
    assert settings.restate_url == "env:http://restate"
    assert settings.dbos_database_url == "file:postgresql://poc/dbos"

    # 3) 无 env 无 file → 默认值（None）
    settings = WorkflowBackendSettings.resolve(environ={}, config_path=None)
    assert settings.temporal_address is None
    assert settings.dbos_database_url is None
    assert settings.restate_url is None

    # 4) 空 env 值不生效，回退 file
    settings = WorkflowBackendSettings.resolve(
        environ={TEMPORAL_ADDRESS_ENV: ""}, config_path=config_file
    )
    assert settings.temporal_address == "file:7233"


def test_backend_config_file_path_from_env(tmp_path) -> None:
    """配置文件路径可由 FLUXION_WORKFLOW_BACKEND_CONFIG 指定。"""
    config_file = tmp_path / "custom.json"
    config_file.write_text(json.dumps({"temporal_address": "file:7233"}), encoding="utf-8")
    settings = WorkflowBackendSettings.resolve(
        environ={WORKFLOW_CONFIG_FILE_ENV: str(config_file)}
    )
    assert settings.temporal_address == "file:7233"
    assert settings.dbos_database_url is None


def test_workflow_error_codes_centralized() -> None:
    """workflow 错误码集中在 errors/workflow.py：码值唯一、异常族完整。"""
    codes = [
        workflow_errors.WORKFLOW_RUN_NOT_FOUND,
        workflow_errors.WORKFLOW_INVALID_STATE,
        workflow_errors.WORKFLOW_CANCEL_TIMEOUT,
        workflow_errors.WORKFLOW_BACKEND_UNAVAILABLE,
    ]
    assert len(set(codes)) == 4, "workflow 错误码不得重复"
    assert all(40_100 <= code < 40_200 for code in codes), "workflow 子段 40_1xx"

    # 异常族：code 属性来自集中定义，异常类型即语义（禁止调用方比对字符串）
    error = workflow_errors.WorkflowBackendUnavailableError("backend down")
    assert isinstance(error, WorkflowEngineError)
    assert error.code == workflow_errors.WORKFLOW_BACKEND_UNAVAILABLE

    for exc_type in (
        workflow_errors.WorkflowRunNotFoundError,
        workflow_errors.WorkflowInvalidStateError,
        workflow_errors.WorkflowCancelTimeoutError,
        workflow_errors.WorkflowBackendUnavailableError,
    ):
        assert issubclass(exc_type, WorkflowEngineError)


def test_workflow_event_log_contains_correlation_fields(caplog) -> None:
    """backend-logging：workflow 事件日志含 run_id/tenant_id/trace_id 结构化字段。"""
    with caplog.at_level(logging.INFO, logger=WORKFLOW_LOGGER_NAME):
        emit_workflow_event_log(
            event="workflow.started",
            run_id="wf-run-1",
            tenant_id="tenant-a",
            trace_id="trace-1",
            execution_id="exec-1",
        )
    records = [r for r in caplog.records if r.name == WORKFLOW_LOGGER_NAME]
    assert records, "workflow 事件日志必须发出"
    entry = json.loads(records[0].getMessage())
    assert entry["event"] == "workflow.started"
    assert entry["run_id"] == "wf-run-1"
    assert entry["tenant_id"] == "tenant-a"
    assert entry["trace_id"] == "trace-1"
    assert entry["execution_id"] == "exec-1"
    assert entry["service"] == "fluxion-runtime"
