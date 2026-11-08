"""Real local-budget contracts. No network or datastore substitute is required."""
import asyncio

import pytest


def _controller(policy, **kwargs):
    from muad_contracts.egress_budget import resolve_egress_budget
    from muad_platform_sdk.request_budget import create_budget_controller
    return create_budget_controller(resolve_egress_budget(policy), segment_id="segment", **kwargs)


def _http(invocation=None, path="/assets/1?q=secret"):
    from muad_platform_sdk.request_budget import AttemptContext
    return AttemptContext(platform_key="p", method="GET", path=path, invocation=invocation)


async def test_s_01_requests_are_consumed_on_error_and_cancel():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    events = []
    controller = _controller({"execution": {"max_requests": 2, "max_inflight": 1}}, observer=events.append)
    with pytest.raises(RuntimeError):
        async with controller.attempt(_http()):
            raise RuntimeError("request failed")
    with pytest.raises(asyncio.CancelledError):
        async with controller.attempt(_http()):
            raise asyncio.CancelledError()
    with pytest.raises(LocalRateLimitError) as error:
        async with controller.attempt(_http()):
            pytest.fail("exhausted request sent")
    assert error.value.used == 2
    assert error.value.limit_kind == "TOTAL"
    assert controller.inflight == 0
    assert "secret" not in repr(events) + str(error.value)
    assert [e.outcome for e in events if e.phase == "completed"] == ["error", "cancelled"]


async def test_e_01_concurrency_releases_only_its_own_lease():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    controller = _controller({"execution": {"max_inflight": 1}})
    sends = 0
    async with controller.attempt(_http()) as first:
        with pytest.raises(LocalRateLimitError):
            async with controller.attempt(_http()):
                sends += 1
        first.release()
        async with controller.attempt(_http()) as second:
            first.release()
            assert controller.inflight == 1
            assert second is not first
    assert sends == 0
    assert controller.inflight == 0


async def test_b_01_atomic_parent_and_child_rules():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    controller = _controller({"execution": {"max_requests": 2},
                              "skill_defaults": {"total": {"max_requests": 1}}})
    a, b = controller.invocation("a", "a1"), controller.invocation("b", "b1")
    async with controller.attempt(_http(a)):
        pass
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(_http(a)):
            pytest.fail("child exhausted")
    async with controller.attempt(_http(b)):
        pass
    assert controller.used == 2
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(_http(b)):
            pytest.fail("parent exhausted")
    assert a.exhausted.is_set()


async def test_b_01_concurrent_reservations_do_not_overshoot():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    controller = _controller({"execution": {"max_requests": 3}})
    async def send():
        try:
            async with controller.attempt(_http()):
                await asyncio.sleep(0)
            return True
        except LocalRateLimitError:
            return False
    assert sum(await asyncio.gather(*(send() for _ in range(20)))) == 3
    assert controller.used == 3


def test_b_02_no_limits_does_not_construct_a_controller():
    assert _controller({}) is None
    assert _controller({"execution": {"max_requests": None}}) is None


async def test_b_05_matching_and_exact_window_boundary():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    now = [10.0]
    rules = [{"id": "one", "match": {"platform_key": "p", "method": "GET", "path_template": "/assets/{id}"},
              "limits": {"window_requests": 1, "window_seconds": 2}}]
    controller = _controller({"targets": rules}, clock=lambda: now[0])
    async with controller.attempt(_http()):
        pass
    now[0] = 11.999
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(_http(path="/assets/2?q=other")):
            pytest.fail("window cap bypassed")
    now[0] = 12.0
    async with controller.attempt(_http(path="/assets/2")):
        pass
    assert controller.used == 2


async def test_b_05_mcp_handshake_does_not_match_a_tool():
    from muad_platform_sdk.request_budget import AttemptContext, LocalRateLimitError
    controller = _controller({"targets": [
        {"id": "tool", "match": {"mcp_server_key": "m", "tool_name": "search"},
         "limits": {"max_requests": 1}}]})
    async with controller.attempt(AttemptContext(mcp_server_key="m", rpc_method="initialize")):
        pass
    context = AttemptContext(mcp_server_key="m", rpc_method="tools/call", tool_name="search")
    async with controller.attempt(context):
        pass
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(context):
            pytest.fail("tool cap bypassed")


async def test_e_04_observer_failure_does_not_resend_or_leak(caplog):
    def observer(event):
        raise RuntimeError("credential=do-not-log-this")
    controller = _controller({"execution": {"max_requests": 1}}, observer=observer)
    async with controller.attempt(_http()):
        pass
    assert controller.used == 1
    assert controller.inflight == 0
    assert controller.observer_failures == 2
    assert "egress_budget_observer_failed" in caplog.text
    assert "do-not-log-this" not in caplog.text


async def test_invalid_context_cannot_bypass_target_matching():
    from muad_platform_sdk.request_budget import AttemptContext
    with pytest.raises(ValueError):
        AttemptContext(platform_key="p", mcp_server_key="m")
    with pytest.raises(ValueError):
        AttemptContext()
    with pytest.raises(ValueError):
        AttemptContext(mcp_server_key="m", rpc_method="initialize", tool_name="search")


async def test_observer_cancellation_cleans_the_acquired_lease():
    def observer(event):
        if event.phase == "allowed":
            raise asyncio.CancelledError()
    controller = _controller({"execution": {"max_inflight": 1}}, observer=observer)
    with pytest.raises(asyncio.CancelledError):
        async with controller.attempt(_http()):
            pytest.fail("cancelled observer must not send")
    assert controller.inflight == 0


async def test_overlapping_target_denial_does_not_debit_parent():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    controller = _controller({"execution": {"max_requests": 2}, "targets": [
        {"id": "specific", "match": {"platform_key": "p", "path_template": "/assets/1"},
         "limits": {"max_requests": 1}},
        {"id": "general", "match": {"platform_key": "p", "path_template": "/assets/{id}"},
         "limits": {"max_requests": 2}},
    ]})
    async with controller.attempt(_http(path="/assets/1")):
        pass
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(_http(path="/assets/1")):
            pytest.fail("specific rule exhausted")
    async with controller.attempt(_http(path="/assets/2")):
        pass
    assert controller.used == 2


async def test_unmatched_rules_do_not_consume_or_call_observer():
    events = []
    controller = _controller({"targets": [
        {"id": "mcp", "match": {"mcp_server_key": "m"}, "limits": {"max_requests": 1}}
    ]}, observer=events.append)
    async with controller.attempt(_http()):
        pass
    assert controller.used == 0
    assert controller.inflight == 0
    assert events == []


async def test_invocation_from_other_controller_is_rejected():
    a = _controller({"execution": {"max_requests": 1}})
    b = _controller({"execution": {"max_requests": 1}})
    context = _http(a.invocation("skill", "invocation"))
    with pytest.raises(ValueError):
        async with b.attempt(context):
            pytest.fail("foreign scope must not send")
    assert b.used == 0


async def test_s_01_upstream_limit_still_consumes_a_request():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    events = []
    controller = _controller({"execution": {"max_requests": 1}}, observer=events.append)
    async with controller.attempt(_http()) as lease:
        lease.outcome = "proxy_rate_limited"
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(_http()):
            pytest.fail("upstream failure cannot refund local budget")
    assert controller.used == 1
    assert events[1].phase == "completed"
    assert events[1].outcome == "proxy_rate_limited"


async def test_b_05_query_and_encoded_identifier_do_not_create_new_buckets():
    from muad_platform_sdk.request_budget import LocalRateLimitError
    controller = _controller({"targets": [
        {"id": "path", "match": {"platform_key": "p", "path_template": "/assets/{id}"},
         "limits": {"max_requests": 1}}
    ]})
    async with controller.attempt(_http(path="/assets/%31?q=a")):
        pass
    with pytest.raises(LocalRateLimitError):
        async with controller.attempt(_http(path="/assets/2?q=b")):
            pytest.fail("dynamic paths must share the static target rule bucket")


async def test_e_04_diagnostic_outcome_cannot_carry_arbitrary_secret_text():
    events = []
    controller = _controller({"execution": {"max_requests": 1}}, observer=events.append)
    async with controller.attempt(_http()) as lease:
        lease.outcome = "sensitive response payload"
    assert events[-1].outcome == "unknown"
    assert "sensitive response payload" not in repr(events)
