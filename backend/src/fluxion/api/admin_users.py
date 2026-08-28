"""Product API：`/admin/users/*`（User Domain，TASK-007）。

统一 envelope / request_id 沿用 responses+middleware；独立部署时未装配
UserDomainService → 显式 503，不静默。
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from fluxion.agents.definitions import AgentCapabilityReference, CapabilityType
from fluxion.api.console_helpers import _actor
from fluxion.api.responses import success
from fluxion.errors.console import RUNTIME_APPLICATION_ERROR, ConsoleError
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.users import UserDomainService


class AdminUserCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_user_id: str | None = None
    display_name: str = Field(min_length=1, max_length=255)


class ProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)
    bio: str = ""
    timezone: str | None = None
    language: str | None = None


class PreferencesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = "system"
    notification_enabled: bool = True
    personalization_policy_ref: str | None = None


class GrantPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: CapabilityType
    capability_ref: str = Field(min_length=1, max_length=255)
    version_pin: str = Field(min_length=1, max_length=64)
    granted_scope: str = "invoke"


def _require(user_service: object | None) -> UserDomainService:
    if user_service is None:
        raise ConsoleError(
            RUNTIME_APPLICATION_ERROR,
            "admin user api requires user domain service",
            503,
        )
    return user_service  # type: ignore[return-value]


def register_admin_user_routes(
    app: FastAPI,
    console_service: ConsoleApplicationService,
    *,
    user_service: UserDomainService | None = None,
) -> None:
    @app.post("/admin/users")
    async def create_admin_user(
        payload: AdminUserCreatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        record = await svc.ensure_user(
            tenant_id=actor.tenant_id,
            platform_user_id=payload.platform_user_id or f"u_{uuid4().hex[:12]}",
            display_name=payload.display_name,
            actor_id=actor.actor_id,
            request_id=actor.request_id,
        )
        return success(
            {
                "platform_user_id": record.platform_user_id,
                "display_name": record.display_name,
            }
        )

    @app.get("/admin/users")
    async def list_admin_users(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        users, total = await svc.list_users(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return success(
            {
                "items": [
                    {"platform_user_id": u.platform_user_id, "display_name": u.display_name}
                    for u in users
                ],
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )

    @app.get("/admin/users/by-channel")
    async def resolve_by_channel(
        channel_type: Annotated[str, Query()],
        channel_user_id: Annotated[str, Query()],
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        # BE-E-06：未绑定渠道身份 → 404 user_not_bound（集中码 34_101）。
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        identity = await svc.resolve_channel_identity_or_raise(
            tenant_id=actor.tenant_id,
            channel_type=channel_type,
            channel_user_id=channel_user_id,
        )
        return success({"platform_user_id": identity.platform_user_id})

    @app.get("/admin/users/{platform_user_id}")
    async def get_admin_user(
        platform_user_id: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        view = await svc.user_360(tenant_id=actor.tenant_id, platform_user_id=platform_user_id)
        return success(view["identity"])

    @app.put("/admin/users/{platform_user_id}/profile")
    async def put_profile(
        platform_user_id: str,
        payload: ProfilePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        spec = payload.model_dump(mode="json")
        profile = await svc.upsert_profile(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            spec={k: v for k, v in spec.items() if v is not None},
            actor_id=actor.actor_id,
            request_id=actor.request_id,
        )
        return success(profile)

    @app.get("/admin/users/{platform_user_id}/profile")
    async def get_profile(
        platform_user_id: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        return success(await svc.get_profile(tenant_id=actor.tenant_id, platform_user_id=platform_user_id))

    @app.put("/admin/users/{platform_user_id}/preferences")
    async def put_preferences(
        platform_user_id: str,
        payload: PreferencesPayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        record = await svc.set_preferences(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            spec=payload.model_dump(mode="json"),
            actor_id=actor.actor_id,
            request_id=actor.request_id,
        )
        return success(record)

    @app.post("/admin/users/{platform_user_id}/grants")
    async def post_grant(
        platform_user_id: str,
        payload: GrantPayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        result = await svc.grant(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            capability_binding=AgentCapabilityReference(
                capability_ref=payload.capability_ref,
                version_pin=payload.version_pin,
                type=payload.type,
            ),
            granted_scope=payload.granted_scope,
            actor_id=actor.actor_id,
            request_id=actor.request_id,
        )
        return success(result)

    @app.delete("/admin/users/{platform_user_id}/grants/{capability_ref}")
    async def delete_grant(
        platform_user_id: str,
        capability_ref: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        revoked = await svc.revoke_grant(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            capability_ref=capability_ref,
            actor_id=actor.actor_id,
            request_id=actor.request_id,
        )
        return success({"revoked": revoked})

    @app.get("/admin/users/{platform_user_id}/360")
    async def get_user_360(
        platform_user_id: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        svc = _require(user_service)
        actor = _actor(x_actor_id)
        return success(await svc.user_360(tenant_id=actor.tenant_id, platform_user_id=platform_user_id))
