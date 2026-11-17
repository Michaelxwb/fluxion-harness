"""Console policy HTTP matching; no operator-owned rule file."""

import pytest
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_platform_sdk.console_egress import resolve_policy_target
from muad_platform_sdk.egress_ipc import AttemptMetadata
from muad_platform_sdk.shared_redis import MeteringUnavailable


def route(path="/assets/{id}"):
    return {"origin": "https://platform.test", "method": "GET", "path_template": path}


def policy(routes):
    return SharedEgressPolicy.model_validate(
        {
            "revision": 1,
            "rules": [
                {
                    "id": str(index),
                    "target_instance_id": "platform-instance",
                    "route_id": "GET " + item["path_template"],
                    "max_inflight": 4,
                    "http_match": item,
                }
                for index, item in enumerate(routes)
            ],
        }
    )


def test_public_template_resolves_without_query_or_script_ids():
    settings = policy([route()])
    for path in ("/assets/123", "/assets/456"):
        target = resolve_policy_target(
            settings, AttemptMetadata(origin="https://platform.test", method="GET", path=path)
        )
        assert (target.target_instance_id, target.route_id) == ("platform-instance", "GET /assets/{id}")
    assert (
        resolve_policy_target(
            settings, AttemptMetadata(origin="https://other.test", method="GET", path="/assets/123")
        )
        is None
    )


def test_more_specific_route_wins_and_authority_override_is_denied():
    settings = policy([route(), route("/assets/export")])
    metadata = AttemptMetadata(origin="https://platform.test", method="GET", path="/assets/export")
    assert resolve_policy_target(settings, metadata).route_id == "GET /assets/export"
    assert resolve_policy_target(settings, metadata.model_copy(update={"authority": "another.test"})) is None


def test_configured_authority_is_allowed_and_other_override_is_denied():
    settings = policy(
        [{**route("/gateway/v1/order"), "authority": "inner.sangfor.com.cn"}],
    )
    request = AttemptMetadata(
        origin="https://platform.test", method="GET", path="/gateway/v1/order",
        authority="inner.sangfor.com.cn",
    )
    assert resolve_policy_target(settings, request).target_instance_id == "platform-instance"
    assert resolve_policy_target(settings, request.model_copy(update={"authority": "other.test"})) is None


def test_configured_authority_allows_unmatched_session_health_target():
    settings = policy(
        [route("/gateway/v1/order")],
    )
    request = AttemptMetadata(
        origin="https://platform.test", method="POST",
        path="/gateway/mss-auth-acl-service/v1/certification/login_agent",
        authority="inner.sangfor.com.cn",
    )
    assert resolve_policy_target(settings, request) is None


def test_same_origin_platforms_are_selected_by_configured_authority():
    settings = SharedEgressPolicy.model_validate({
        "revision": 1,
        "rules": [
            {
                "id": "one", "target_instance_id": "one", "route_id": "GET /order",
                "max_inflight": 1,
                "http_match": {
                    "origin": "https://gateway.test", "authority": "one.internal",
                    "method": "GET", "path_template": "/order",
                },
            },
            {
                "id": "two", "target_instance_id": "two", "route_id": "GET /order",
                "max_inflight": 1,
                "http_match": {
                    "origin": "https://gateway.test", "authority": "two.internal",
                    "method": "GET", "path_template": "/order",
                },
            },
        ],
    })
    target = resolve_policy_target(
        settings,
        AttemptMetadata(
            origin="https://gateway.test", method="GET", path="/order", authority="two.internal"
        ),
    )
    assert (target.target_instance_id, target.route_id) == ("two", "GET /order")


@pytest.mark.parametrize(
    "routes", [[route(), route("/assets/{name}")], [route("/{x}/export"), route("/assets/{y}")]]
)
def test_ambiguous_current_policy_is_rejected(routes):
    with pytest.raises(MeteringUnavailable, match="ambiguous"):
        resolve_policy_target(
            policy(routes),
            AttemptMetadata(origin="https://platform.test", method="GET", path="/assets/export"),
        )


@pytest.mark.parametrize("path", ["/assets/*", "/assets/{id}/..", "/assets?token=x"])
def test_ambiguous_or_query_bearing_templates_are_rejected(path):
    with pytest.raises(ValueError):
        policy([route(path)])
