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
