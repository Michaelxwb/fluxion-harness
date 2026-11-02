"""压缩配置与模型池执行策略的读取缝：平台设置快照（调用方在边界取好）+ Agent 覆盖 + 默认值。

ADR-03：**不保留任何进程内设置缓存**——平台设置快照在业务操作边界取一次（Runtime 是 Run
创建事务内），随调用栈显式传入 `platform_overrides`，解析结果立即冻结进 `policy_json`。
因此「保存后等一会儿才生效」的 TTL 语义被彻底删除；在飞的 Run 用的仍是它自己那份冻结值。

模型池策略（设计 §3.7 `model_pool` / `capacity` 两组里 Run 冻结的叶）走同一条缝：路由五叶
连同 `dispatch_policy_hash` 的规范身份、模型等待预算一起冻进 `policy_json`。**非密钥**是硬约束：
Key 明文、去重指纹、由其派生的计量身份（`key_resource_id`）与启动资源预算（`SharedSettings`）
都不在这里——调用凭据实时经受信接口按冻结引用解析（API-09）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from muad_contracts.platform_settings import (
    COMPACTION_POLICY_KEY,
    CompactionConfigError,
    CompactionSettings,
    LearningSettings,
    PlatformSettingsError,
    merge_compaction_payload,
    parse_compaction_settings,
    parse_platform_settings,
)

from .model_allocator import dispatch_policy_hash

#: `policy_json` 里冻结的模型池路由段（与平台设置分组同名，便于与 §3.7 逐项对照）
MODEL_POOL_POLICY_KEY = "model_pool"
#: `policy_json` 里冻结的容量段：本需求只写 Run 创建时冻结的**模型等待预算**
CAPACITY_POLICY_KEY = "capacity"
#: 模型等待预算叶（`capacity.model_wait_timeout_sec`）：每段等待取本段与原 deadline 较小者
MODEL_WAIT_TIMEOUT_LEAF = "model_wait_timeout_sec"


LEARNING_POLICY_KEY = "learning"


@dataclass(frozen=True, slots=True)
class FrozenLearning:
    """Run 侧冻结的 learning 设置（缺失/旧行一律回落 schema 默认，默认关闭学习）。"""

    enabled: bool = False
    min_score: float = 0.35
    preference_items: int = 3
    preference_bytes: int = 512
    auto_activate: tuple[str, ...] = ("PROCEDURE", "FAILURE_PATTERN")
    trial_runs: int = 20
    trial_max_regression: float = 0.05
    min_success_runs: int = 3
    eval_min_improvement: float = 0.05
    eval_min_cases: int = 10
    eval_max_cases: int = 50
    eval_max_items_per_case: int = 10


def frozen_learning_payload(document: Mapping[str, Any] | None) -> dict[str, Any]:
    """平台设置快照 → 冻进 policy_json 的 learning 段（按 schema 默认补齐）。"""
    settings = parse_platform_settings(document or {})
    return asdict(settings.learning)


def frozen_learning_from_settings(settings: LearningSettings) -> FrozenLearning:
    """平台设置（新建 Run 边界）→ 冻结形态，供 ExecutionDefaults 注入。"""
    return learning_of({LEARNING_POLICY_KEY: asdict(settings)})


def learning_of(policy_json: Mapping[str, Any] | None) -> FrozenLearning:
    raw = policy_json.get(LEARNING_POLICY_KEY) if isinstance(policy_json, Mapping) else None
    if not isinstance(raw, Mapping):
        return FrozenLearning()

    def _bool(key: str, default: bool) -> bool:
        value = raw.get(key)
        return value if isinstance(value, bool) else default

    def _num(key: str, default: float) -> float:
        value = raw.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default

    auto = raw.get("experience_auto_activate")
    auto_activate = (
        tuple(item for item in auto if isinstance(item, str))
        if isinstance(auto, (list, tuple))
        else FrozenLearning().auto_activate
    )
    return FrozenLearning(
        enabled=_bool("learning_enabled", False),
        min_score=_num("retrieval_min_score", 0.35),
        preference_items=int(_num("retrieval_preference_items", 3)),
        preference_bytes=int(_num("retrieval_preference_bytes", 512)),
        auto_activate=auto_activate,
        trial_runs=int(_num("experience_trial_runs", 20)),
        trial_max_regression=_num("experience_trial_max_regression", 0.05),
        min_success_runs=int(_num("experience_min_success_runs", 3)),
        eval_min_improvement=_num("eval_min_improvement", 0.05),
        eval_min_cases=int(_num("eval_min_cases", 10)),
        eval_max_cases=int(_num("eval_max_cases", 50)),
        eval_max_items_per_case=int(_num("eval_max_items_per_case", 10)),
    )


@dataclass(frozen=True, slots=True)
class PoolExecutionPolicy:
    """Run 创建边界冻结的模型池执行策略（非密钥）：路由五叶 + 模型等待预算。

    `policy_hash` 由 `dispatch_policy_hash` **唯一确定**（TASK-023 的调度计数账本键）：
    不同策略版本的分布分别解释，不自洽的哈希不会被当成另一个版本放行。
    """

    dispatch_epoch_sec: int
    input_estimate_multiplier: float
    interactive_weight: int
    background_weight: int
    aging_interval_sec: int
    model_wait_timeout_sec: int

    @property
    def policy_hash(self) -> str:
        return dispatch_policy_hash(
            dispatch_epoch_sec=self.dispatch_epoch_sec,
            interactive_weight=self.interactive_weight,
            background_weight=self.background_weight,
            aging_interval_sec=self.aging_interval_sec,
        )


def resolve_pool_execution_policy(
    platform_overrides: Mapping[str, Any] | None,
) -> PoolExecutionPolicy:
    """从平台设置快照解析模型池路由与模型等待预算；缺省回落 schema 默认，非法显式报错。

    与压缩配置同一条纪律：坏值/未知键由 `parse_platform_settings` 显式拒绝，绝不静默回退。
    """
    parsed = parse_platform_settings(platform_overrides)
    pool = parsed.model_pool
    return PoolExecutionPolicy(
        dispatch_epoch_sec=pool.dispatch_epoch_sec,
        input_estimate_multiplier=float(pool.input_estimate_multiplier),
        interactive_weight=pool.interactive_weight,
        background_weight=pool.background_weight,
        aging_interval_sec=pool.aging_interval_sec,
        model_wait_timeout_sec=parsed.capacity.model_wait_timeout_sec,
    )


def pool_policy_payload(policy: PoolExecutionPolicy) -> dict[str, Any]:
    """写进 `policy_json` 的两段（路由 + 等待预算），含冻结的 `policy_hash`。"""
    return {
        MODEL_POOL_POLICY_KEY: {
            "dispatch_epoch_sec": policy.dispatch_epoch_sec,
            "input_estimate_multiplier": policy.input_estimate_multiplier,
            "interactive_weight": policy.interactive_weight,
            "background_weight": policy.background_weight,
            "aging_interval_sec": policy.aging_interval_sec,
            "policy_hash": policy.policy_hash,
        },
        CAPACITY_POLICY_KEY: {MODEL_WAIT_TIMEOUT_LEAF: policy.model_wait_timeout_sec},
    }


def pool_execution_policy_of(policy: Mapping[str, Any] | None) -> PoolExecutionPolicy | None:
    """从**已冻结**的 `policy_json` 还原模型池策略（执行期与 resume 都走这条）。

    缺段落（本需求上线前创建的行）返回 `None`：旧行**不补新默认**，由消费方决定如何回压。
    形状坏、缺叶或哈希与字段不自洽则显式抛 `PlatformSettingsError`——冻结数据是 Run 的执行
    语义，宁可失败也不按猜出的值解释。
    """
    source: Mapping[str, Any] = policy if isinstance(policy, Mapping) else {}
    section = source.get(MODEL_POOL_POLICY_KEY)
    if section is None:
        return None
    if not isinstance(section, Mapping):
        raise PlatformSettingsError("frozen model pool policy must be an object")
    restored = PoolExecutionPolicy(
        dispatch_epoch_sec=_require_positive_int(section, "dispatch_epoch_sec"),
        input_estimate_multiplier=_require_positive_number(section, "input_estimate_multiplier"),
        interactive_weight=_require_positive_int(section, "interactive_weight"),
        background_weight=_require_positive_int(section, "background_weight"),
        aging_interval_sec=_require_positive_int(section, "aging_interval_sec"),
        model_wait_timeout_sec=_require_wait_timeout(source),
    )
    if section.get("policy_hash") != restored.policy_hash:
        raise PlatformSettingsError("frozen model pool policy hash does not verify")
    return restored


def _require_positive_int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PlatformSettingsError(f"frozen model pool leaf {key} must be a positive integer")
    return value


def _require_positive_number(source: Mapping[str, Any], key: str) -> float:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlatformSettingsError(f"frozen model pool leaf {key} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PlatformSettingsError(f"frozen model pool leaf {key} must be a positive finite number")
    return number


def _require_wait_timeout(policy: Mapping[str, Any]) -> int:
    section = policy.get(CAPACITY_POLICY_KEY)
    if not isinstance(section, Mapping):
        raise PlatformSettingsError("frozen capacity policy must be an object")
    value = section.get(MODEL_WAIT_TIMEOUT_LEAF)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PlatformSettingsError(
            f"frozen capacity leaf {MODEL_WAIT_TIMEOUT_LEAF} must be a positive integer"
        )
    return value


def resolve_compaction_settings(
    agent_runtime_config: Mapping[str, Any] | None,
    *,
    platform_overrides: Mapping[str, Any] | None,
) -> CompactionSettings:
    """把**平台设置快照**（`compaction` 分组）与 Agent 显式覆盖合并成完整压缩配置。

    `platform_overrides` 由调用方在业务边界取好（Runtime = `_create_run` 事务内）；
    Agent 的 `runtime_config.budget.compaction` 优先级更高。非法配置显式报错。
    """
    budget = (agent_runtime_config or {}).get("budget") or {}
    if not isinstance(budget, Mapping):
        raise CompactionConfigError("runtime_config.budget 必须是对象")
    payload = merge_compaction_payload(base=platform_overrides, override=budget.get("compaction"))
    return parse_compaction_settings(payload)


def compaction_settings_of(policy: Mapping[str, Any] | None) -> CompactionSettings | None:
    """从**已冻结**的 `policy_json` 还原压缩配置；缺键或形状坏返回 None（等于不压缩）。

    resume 走这条：在跑的 Run 用的永远是它自己那一份冻结值，不吃当前配置。
    """
    compaction = (policy or {}).get(COMPACTION_POLICY_KEY)
    if not isinstance(compaction, Mapping):
        return None
    try:
        return parse_compaction_settings(compaction)
    except CompactionConfigError:
        return None


__all__ = [
    "CAPACITY_POLICY_KEY",
    "MODEL_POOL_POLICY_KEY",
    "MODEL_WAIT_TIMEOUT_LEAF",
    "PoolExecutionPolicy",
    "compaction_settings_of",
    "pool_execution_policy_of",
    "pool_policy_payload",
    "resolve_compaction_settings",
    "resolve_pool_execution_policy",
]
