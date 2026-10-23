"""池化预占的输出预算契约：缺失/非法 `max_tokens` 是**模型配置**错误。

用户请求本身是合法的；把配置缺陷落成 `COMMON_VALIDATION_ERROR` 会让 IM 用户看到
「请求参数校验失败」，既误导用户也掩盖了真正要改的模型定义。
"""

from __future__ import annotations

import pytest
from muad_agent_core.model import ModelMessage, ModelRequest, ModelRole
from muad_agent_runtime.application.context_settings import PoolExecutionPolicy
from muad_agent_runtime.application.executor import reserved_tokens_for
from muad_api import AppError
from muad_api.error_codes import ErrorCode


def _policy() -> PoolExecutionPolicy:
    return PoolExecutionPolicy(
        dispatch_epoch_sec=60,
        input_estimate_multiplier=1.25,
        interactive_weight=8,
        background_weight=1,
        aging_interval_sec=30,
        model_wait_timeout_sec=30,
    )


def _request(max_tokens: int | None) -> ModelRequest:
    return ModelRequest(
        model_id="gpt-4o-mini",
        messages=(ModelMessage(role=ModelRole.USER, content="你好"),),
        max_tokens=max_tokens,
    )


@pytest.mark.parametrize("budget", [None, 0, -1])
def test_reserved_tokens_reports_invalid_output_budget_as_configuration_error(
    budget: int | None,
) -> None:
    with pytest.raises(AppError) as raised:
        reserved_tokens_for(_request(budget), policy=_policy())

    assert raised.value.code == ErrorCode.MODEL_OUTPUT_BUDGET_INVALID


def test_reserved_tokens_includes_the_sent_output_budget() -> None:
    assert reserved_tokens_for(_request(64), policy=_policy()) >= 64
