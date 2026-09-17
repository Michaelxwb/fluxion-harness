from __future__ import annotations

import os
from collections.abc import Mapping

from ..types import SecretValue
from .base import SecretNotFoundError

ENV_SECRET_PREFIX = "MUAD_SECRET__"
ENV_VERSION_SUFFIX = "_VERSION"
DEFAULT_SECRET_VERSION = "1"
SECRET_REF_SCHEME = "secret://"
SEGMENT_SEPARATOR = "/"


def secret_env_var(secret_ref: str) -> str:
    if not secret_ref.startswith(SECRET_REF_SCHEME):
        raise ValueError(f"unsupported secret ref scheme: {secret_ref}")
    path = secret_ref[len(SECRET_REF_SCHEME) :]
    namespace, separator, name = path.partition(SEGMENT_SEPARATOR)
    if not separator or not namespace or not name:
        raise ValueError(f"secret ref must be secret://<namespace>/<name>: {secret_ref}")
    return f"{ENV_SECRET_PREFIX}{_env_segment(namespace)}__{_env_segment(name)}"


def _env_segment(value: str) -> str:
    return "".join(char if char.isascii() and char.isalnum() else "_" for char in value).upper()


class EnvSecretProvider:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    async def get(self, secret_ref: str) -> SecretValue:
        env_name = secret_env_var(secret_ref)
        value = self._environ.get(env_name, "")
        if not value:
            raise SecretNotFoundError(secret_ref)
        version = self._environ.get(f"{env_name}{ENV_VERSION_SUFFIX}") or DEFAULT_SECRET_VERSION
        return SecretValue(value=value, version=version)
