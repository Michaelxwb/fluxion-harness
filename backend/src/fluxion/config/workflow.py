from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Workflow backend 连接配置（ADR-WF-001 TASK-001；backend-platform：环境变量 > 配置文件 > 默认值）。
TEMPORAL_ADDRESS_ENV = "TEMPORAL_ADDRESS"
DBOS_DATABASE_URL_ENV = "DBOS_DATABASE_URL"
RESTATE_URL_ENV = "RESTATE_URL"
WORKFLOW_CONFIG_FILE_ENV = "FLUXION_WORKFLOW_BACKEND_CONFIG"
DEFAULT_CONFIG_FILE = ".fluxion-workflow.json"

_DEFAULTS: Mapping[str, str | None] = {
    "temporal_address": None,
    "dbos_database_url": None,
    "restate_url": None,
}


@dataclass(frozen=True, slots=True)
class WorkflowBackendSettings:
    """PoC 候选 backend 连接配置；三层优先级：环境变量 > 配置文件 > 默认值。"""

    temporal_address: str | None = None
    dbos_database_url: str | None = None
    restate_url: str | None = None

    @classmethod
    def resolve(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        config_path: str | Path | None = None,
    ) -> WorkflowBackendSettings:
        env = os.environ if environ is None else environ
        file_values = _load_config_file(_resolve_config_path(env, config_path))
        return cls(
            temporal_address=_pick(env, TEMPORAL_ADDRESS_ENV, file_values, "temporal_address"),
            dbos_database_url=_pick(env, DBOS_DATABASE_URL_ENV, file_values, "dbos_database_url"),
            restate_url=_pick(env, RESTATE_URL_ENV, file_values, "restate_url"),
        )


def _resolve_config_path(
    env: Mapping[str, str], config_path: str | Path | None
) -> Path | None:
    if config_path is not None:
        return Path(config_path)
    from_env = env.get(WORKFLOW_CONFIG_FILE_ENV)
    return Path(from_env) if from_env else None


def _load_config_file(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


def _pick(
    env: Mapping[str, str],
    env_key: str,
    file_values: Mapping[str, str],
    file_key: str,
) -> str | None:
    """优先级：环境变量 > 配置文件 > 默认值（空串视为未设置）。"""
    env_value = env.get(env_key)
    if env_value:
        return env_value
    file_value = file_values.get(file_key)
    if file_value:
        return file_value
    return _DEFAULTS.get(file_key)


__all__ = [
    "DBOS_DATABASE_URL_ENV",
    "DEFAULT_CONFIG_FILE",
    "RESTATE_URL_ENV",
    "TEMPORAL_ADDRESS_ENV",
    "WORKFLOW_CONFIG_FILE_ENV",
    "WorkflowBackendSettings",
]
