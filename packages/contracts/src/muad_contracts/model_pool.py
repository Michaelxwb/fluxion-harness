"""Model resource and capacity wire contracts; persistence remains authoritative."""

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StrictBool,
    StrictStr,
    model_validator,
)

from .tasks import ContractModel


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("model resource text must not be blank")
    return value


def _non_blank_secret(value: SecretStr) -> SecretStr:
    _non_blank(value.get_secret_value())
    return value


def _http_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port is None or parsed.port > 0
    except ValueError as exc:
        raise ValueError("base_url must be a credential-free HTTP(S) endpoint") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or not valid_port
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError("base_url must be a credential-free HTTP(S) endpoint")
    return value


ResourceKey = Annotated[StrictStr, Field(min_length=1, max_length=128), AfterValidator(_non_blank)]
PositiveCount = Annotated[int, Field(strict=True, ge=1, le=(1 << 63) - 1)]
NonnegativeCount = Annotated[int, Field(strict=True, ge=0, le=(1 << 63) - 1)]
PositiveSlots = Annotated[int, Field(strict=True, ge=1, le=(1 << 31) - 1)]
HttpEndpoint = Annotated[StrictStr, AfterValidator(_http_endpoint)]
ApiKeyValue = Annotated[SecretStr, AfterValidator(_non_blank_secret)]


class ModelPoolContract(ContractModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, allow_inf_nan=False)


class ModelQuotaGroupInput(ModelPoolContract):
    key: ResourceKey
    max_inflight: PositiveSlots
    rpm: PositiveCount | None = None
    tpm: PositiveCount | None = None


class ModelQuotaGroupConfig(ModelQuotaGroupInput):
    id: UUID
    revision: PositiveCount


class ModelCredentialFields(ModelPoolContract):
    key: ResourceKey
    provider_key: ResourceKey
    base_url: HttpEndpoint
    #: 无共享账号额度组时该 Key 独占自身容量（0021 迁移清单允许 `quota_group: null`）
    quota_group_id: UUID | None = None
    max_inflight: PositiveSlots
    enabled: StrictBool = True


class ModelCredentialCreate(ModelCredentialFields):
    api_key: ApiKeyValue = Field(exclude=True, repr=False)


class ModelCredentialUpdate(ModelCredentialFields):
    """PUT 整份替换可编辑字段；缺省 `api_key` 表示保留原值（留空不是合法表达）。"""

    expected_revision: PositiveCount
    api_key: ApiKeyValue | None = Field(default=None, exclude=True, repr=False)


class ModelCredentialConfig(ModelCredentialFields):
    id: UUID
    revision: PositiveCount
    configured: StrictBool = True


class ModelDeploymentInput(ModelPoolContract):
    key: ResourceKey
    credential_id: UUID
    weight: Annotated[int, Field(strict=True, ge=1, le=100)] = 1
    enabled: StrictBool = True


class ModelDeploymentConfig(ModelDeploymentInput):
    id: UUID
    model_id: UUID
    revision: PositiveCount


class SelectedModelCredential(ModelPoolContract):
    """Only trusted credential resolution explicitly serializes the actual key."""

    model_id: UUID
    deployment_id: UUID
    credential_id: UUID
    credential_revision: PositiveCount
    key_resource_id: UUID
    quota_group_id: UUID
    provider_key: ResourceKey
    base_url: HttpEndpoint
    api_key: ApiKeyValue = Field(exclude=True, repr=False)

    def to_internal_payload(self) -> dict[str, JsonValue]:
        """Internal authenticated HTTP only; normal dumps/repr exclude api_key."""
        return {
            "model_id": str(self.model_id), "deployment_id": str(self.deployment_id),
            "credential_id": str(self.credential_id), "credential_revision": self.credential_revision,
            "key_resource_id": str(self.key_resource_id), "quota_group_id": str(self.quota_group_id),
            "provider_key": self.provider_key, "base_url": self.base_url,
            "api_key": self.api_key.get_secret_value(),
        }


class RunCallOwner(ModelPoolContract):
    kind: Literal["RUN"] = "RUN"
    run_id: UUID


class MemoryJobCallOwner(ModelPoolContract):
    kind: Literal["MEMORY_JOB"] = "MEMORY_JOB"
    memory_job_id: UUID


class ProbeCallOwner(ModelPoolContract):
    kind: Literal["PROBE"] = "PROBE"
    probe_id: UUID


ModelCallOwner = Annotated[RunCallOwner | MemoryJobCallOwner | ProbeCallOwner, Field(discriminator="kind")]


class ModelCapacityReservationRequest(ModelPoolContract):
    call_attempt_id: UUID
    model_id: UUID
    deployment_id: UUID
    credential_id: UUID
    credential_revision: PositiveCount
    key_resource_id: UUID
    quota_group_id: UUID
    owner: ModelCallOwner
    workload: Literal["INTERACTIVE", "BACKGROUND", "PROBE"]
    deadline_at: AwareDatetime
    reserved_tokens: PositiveCount


class ModelLeaseFence(ModelPoolContract):
    epoch: PositiveCount
    lease_owner: ResourceKey


class ModelCapacityLease(ModelCapacityReservationRequest, ModelLeaseFence):
    reservation_id: UUID
    issued_at: AwareDatetime
    lease_until: AwareDatetime
    state: Literal["ACTIVE", "UNKNOWN", "SETTLED"] = "ACTIVE"

    @model_validator(mode="after")
    def _lease_interval(self) -> Self:
        if not self.issued_at < self.lease_until <= self.deadline_at:
            raise ValueError("lease_until must follow issued_at and not exceed deadline_at")
        return self

    @property
    def retains_capacity(self) -> bool:
        # Expired local leases never establish that the remote provider has stopped.
        return self.state in {"ACTIVE", "UNKNOWN"}


class ModelTokenUsage(ModelPoolContract):
    input_tokens: NonnegativeCount
    output_tokens: NonnegativeCount

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RemoteCompletionProof(ModelPoolContract):
    """Evidence shape only; its authority/reference must be checked by the Owner."""

    kind: Literal["PROVIDER_CONFIRMED", "SERVER_HARD_TIMEOUT"]
    reference: Annotated[StrictStr, Field(min_length=1, max_length=256), AfterValidator(_non_blank)]
    completed_at: AwareDatetime


class ModelCallSettlement(ModelLeaseFence):
    outcome: Literal["SUCCEEDED", "FAILED", "NOT_SENT", "UNKNOWN"]
    usage: ModelTokenUsage | None = None
    remote_completion: RemoteCompletionProof | None = None

    @model_validator(mode="after")
    def _completion_shape(self) -> Self:
        if self.outcome == "UNKNOWN" and self.remote_completion is not None:
            raise ValueError("UNKNOWN must not claim confirmed remote completion")
        if self.outcome == "NOT_SENT" and (self.usage is not None or self.remote_completion is not None):
            raise ValueError("NOT_SENT must not report remote usage or completion")
        if self.outcome == "SUCCEEDED" and self.remote_completion is None:
            raise ValueError("SUCCEEDED requires confirmed remote completion")
        return self

    @property
    def can_release_capacity(self) -> bool:
        """In-flight release only; missing usage never implies zero quota consumption."""
        return self.outcome == "NOT_SENT" or self.remote_completion is not None


class ProbeCapacityRequest(ModelPoolContract):
    probe_id: UUID
    model_id: UUID
    credential_id: UUID
    attempt_id: UUID
    deadline_at: AwareDatetime
    reserved_tokens: PositiveCount
