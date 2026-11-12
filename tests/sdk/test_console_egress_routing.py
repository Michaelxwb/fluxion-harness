"""Console-owned addresses reach the actual host HTTP send boundary."""

import asyncio
import json

import httpx
import pytest
from muad_contracts.shared_egress import SharedEgressPolicy


def test_platform_rule_uses_relative_path_and_server_generated_identity():
    from muad_contracts.platform_settings import egress_rules_from_settings, parse_platform_settings

    settings = parse_platform_settings(
        {
            "egress": {
                "enabled": True,
                "rules_json": json.dumps(
                    [
                        {
                            "target_instance_id": "assets",
                            "method": "GET",
                            "path_template": "/assets/{id}",
                            "window_requests": 2,
                            "window_seconds": 60,
                        }
                    ]
                ),
            }
        }
    )
    (rule,) = egress_rules_from_settings(settings.egress)
    assert rule["route_id"] == "GET /assets/{id}"


@pytest.mark.parametrize("path", ["https://other.test/x", "//other.test/x", "/../x", "/a?token=x"])
def test_platform_rule_rejects_absolute_or_ambiguous_paths(path):
    from muad_contracts.platform_settings import PlatformSettingsError, parse_platform_settings

    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(
            {
                "egress": {
                    "rules_json": json.dumps(
                        [{"target_instance_id": "assets", "method": "GET", "path_template": path}]
                    )
                }
            }
        )


async def test_resolved_platform_prefix_and_bound_policy():
    from muad_platform_sdk.console_egress import resolve_policy_target
    from muad_platform_sdk.egress_ipc import AttemptMetadata

    policy = SharedEgressPolicy.model_validate(
        {
            "revision": 2,
            "rules": [
                {
                    "id": "assets-get",
                    "target_instance_id": "assets",
                    "route_id": "GET /assets/{id}",
                    "window_requests": 1,
                    "window_seconds": 60,
                    "http_match": {
                        "origin": "https://assets.test",
                        "method": "GET",
                        "path_template": "/gateway/v1/assets/{id}",
                    },
                }
            ],
        }
    )
    target = resolve_policy_target(
        policy, AttemptMetadata(origin="https://assets.test", method="GET", path="/gateway/v1/assets/123")
    )
    assert target.route_id == "GET /assets/{id}"
    assert target.policy == policy
    assert (
        resolve_policy_target(
            policy, AttemptMetadata(origin="https://other.test", method="GET", path="/gateway/v1/assets/123")
        )
        is None
    )


@pytest.fixture
async def http_meter():
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from muad_common import SharedSettings
    from muad_platform_sdk.shared_redis import SharedRedisMeter
    from redis.asyncio import Redis

    url = SharedSettings().redis_url
    if not url:
        pytest.skip("REDIS_URL required for physical-send integration")
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(429 if self.path.endswith("/upstream") else 200)
            self.send_header("Retry-After", "7")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    redis = Redis.from_url(url, decode_responses=True)
    domain = "console-routing-" + uuid.uuid4().hex
    prefix = f"fx:egress:{{{domain}}}:"
    await redis.hset(prefix + "guard", mapping={"epoch": "test-epoch"})
    try:
        yield (
            SharedRedisMeter(redis, domain=domain, epoch="test-epoch"),
            f"http://127.0.0.1:{server.server_port}",
            received,
        )
    finally:
        keys = [key async for key in redis.scan_iter(prefix + "*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        server.shutdown()
        server.server_close()
        thread.join()


def routed_policy(origin, *, revision=1, path="/gateway/v1/assets/{id}", window=1):
    return SharedEgressPolicy.model_validate(
        {
            "revision": revision,
            "rules": [
                {
                    "id": "platform-route",
                    "target_instance_id": "assets",
                    "route_id": "GET /assets/{id}",
                    "window_requests": window,
                    "window_seconds": 0.2,
                    "http_match": {"origin": origin, "method": "GET", "path_template": path},
                }
            ],
        }
    )


@pytest.mark.integration
async def test_platform_route_really_blocks_second_physical_request(http_meter):
    from muad_platform_sdk.console_egress import resolve_policy_target
    from muad_platform_sdk.egress_ipc import AttemptMetadata
    from muad_platform_sdk.shared_host_transport import HostRoute, SharedHostTransport
    from muad_platform_sdk.shared_http_transport import RequestLifetime
    from muad_platform_sdk.shared_redis import SharedLimitExceeded

    meter, origin, received = http_meter
    await meter.publish(routed_policy(origin))
    await asyncio.sleep(0.25)  # Let the newly introduced sliding window collect a full history.

    async def resolve(request):
        target = resolve_policy_target(
            await meter.current_policy(),
            AttemptMetadata(origin=origin, method=request.method, path=request.url.path),
        )
        return HostRoute(target.target_instance_id, target.route_id, target.policy)

    transport = SharedHostTransport(
        meter, public_rules_enabled=True, resolve=resolve, lifetime=RequestLifetime(5, 1, 0.1)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get(origin + "/gateway/v1/assets/123")).status_code == 200
        with pytest.raises(SharedLimitExceeded):
            await client.get(origin + "/gateway/v1/assets/456")
    assert received == ["/gateway/v1/assets/123"]


@pytest.mark.integration
async def test_route_publication_race_cannot_bypass_matching_limit(http_meter):
    from muad_platform_sdk.shared_host_transport import HostRoute, SharedHostTransport
    from muad_platform_sdk.shared_http_transport import RequestLifetime
    from muad_platform_sdk.shared_redis import MeteringUnavailable

    meter, origin, received = http_meter
    old = routed_policy(origin)
    await meter.publish(old)

    async def resolve(request):
        await meter.publish(routed_policy(origin, revision=2, window=2))
        return HostRoute("assets", "GET /assets/{id}", old)

    transport = SharedHostTransport(
        meter, public_rules_enabled=True, resolve=resolve, lifetime=RequestLifetime(5, 1, 0.1)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MeteringUnavailable):
            await client.get(origin + "/gateway/v1/assets/123")
    assert received == []


@pytest.mark.integration
async def test_proxy_429_is_preserved_after_platform_routing(http_meter):
    from muad_platform_sdk.shared_host_transport import HostRoute, SharedHostTransport
    from muad_platform_sdk.shared_http_transport import RequestLifetime

    meter, origin, received = http_meter
    policy = routed_policy(origin, path="/gateway/v1/upstream")
    await meter.publish(policy)
    await asyncio.sleep(0.25)
    transport = SharedHostTransport(
        meter,
        public_rules_enabled=True,
        resolve=lambda request: HostRoute("assets", "GET /assets/{id}", policy),
        lifetime=RequestLifetime(5, 1, 0.1),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(origin + "/gateway/v1/upstream")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "7"
        assert response.content == b"ok"
    assert received == ["/gateway/v1/upstream"]
