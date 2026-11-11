"""Operator route configuration; script metadata never selects a domain/epoch."""
import importlib

import pytest
from muad_platform_sdk.egress_ipc import AttemptMetadata


def config(routes):
    module = importlib.import_module("muad_platform_sdk.egress_runtime_config")
    return module.EgressRuntimeConfig.model_validate({
        "domain": "platform", "epoch": "epoch", "public_rules_enabled": True,
        "request_timeout_seconds": 30, "cleanup_seconds": 3, "renewal_interval_seconds": 1,
        "source_refresh_seconds": 10, "stop_margin_seconds": 5, "routes": routes,
    })


def route(path="/assets/{id}"):
    return {"origin": "https://platform.test", "method": "GET", "path_template": path,
            "target_instance_id": "platform-instance", "route_id": "GET " + path}


def test_operator_template_resolves_physical_request_without_query_or_script_ids():
    settings = config([route()])
    for path in ("/assets/123", "/assets/456"):
        target = settings.resolve(AttemptMetadata(origin="https://platform.test", method="GET", path=path))
        assert (target.target_instance_id, target.route_id) == ("platform-instance", "GET /assets/{id}")
    unknown = AttemptMetadata(origin="https://other.test", method="GET", path="/assets/123")
    assert settings.resolve(unknown) is None


def test_more_specific_route_wins_and_authority_override_is_denied():
    settings = config([route(), route("/assets/export")])
    metadata = AttemptMetadata(origin="https://platform.test", method="GET", path="/assets/export")
    assert settings.resolve(metadata).route_id == "GET /assets/export"
    assert settings.resolve(metadata.model_copy(update={"authority": "another.test"})) is None


@pytest.mark.parametrize("routes", [[route(), route("/assets/{name}")],
                                    [route("/{x}/export"), route("/assets/{y}")]])
def test_equal_specificity_ambiguous_routes_are_rejected(routes):
    with pytest.raises(ValueError):
        config(routes)


@pytest.mark.parametrize("path", ["/assets/*", "/assets/{id}/..", "/assets?token=x"])
def test_ambiguous_or_query_bearing_templates_are_rejected(path):
    with pytest.raises(ValueError):
        config([route(path)])
