from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..types import PlatformConfig, SecretValue


@dataclass(frozen=True, slots=True)
class CredentialActor:
    tenant_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    credential_ref: str
    secret: SecretValue

    @property
    def version(self) -> str:
        return self.secret.version


class SecretNotFoundError(LookupError):
    def __init__(self, secret_ref: str) -> None:
        self.secret_ref = secret_ref
        super().__init__(f"secret not found: {secret_ref}")


class SecretProvider(Protocol):
    async def get(self, secret_ref: str) -> SecretValue: ...


class CredentialResolver(Protocol):
    async def resolve(
        self,
        actor: CredentialActor,
        platform: PlatformConfig,
    ) -> ResolvedCredential | None: ...
