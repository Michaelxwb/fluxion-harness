from __future__ import annotations

import asyncio

from fluxion.runtime.mcp_pool import MCPHTTPClientPool, MCPHTTPPoolKey


async def test_S_A18_eviction_skips_in_flight_entry_and_picks_idle_lru() -> None:
    """A18：pool 满时 _evict_for_capacity 只在 in_flight==0 的 entry 中挑 LRU；
    in_flight>0 的 entry（正在被 MCP call 使用）不得被驱逐——避免关掉在用 client。"""
    pool = MCPHTTPClientPool(ttl_seconds=300, max_clients=2)
    key_a = MCPHTTPPoolKey("t", "u", "http://a", "v1", "1")
    key_b = MCPHTTPPoolKey("t", "u", "http://b", "v1", "1")
    client_a = await pool.get_client(
        key_a, headers={}, timeout_ms=1_000, credential_ref=None
    )
    client_b = await pool.get_client(
        key_b, headers={}, timeout_ms=1_000, credential_ref=None
    )
    assert pool.client_count == 2
    # 用 session 占住 client_a（in_flight=1）——模拟一个进行中的 MCP call。
    async with pool.session(
        key_a, headers={}, timeout_ms=1_000, credential_ref=None
    ) as _inflight:
        assert _inflight is client_a
        # 请求第 3 个 key（满）→ _evict_for_capacity：client_a in_flight=1 跳过，
        # 驱逐 client_b（in_flight=0、且 last_used 更老）。
        key_c = MCPHTTPPoolKey("t", "u", "http://c", "v1", "1")
        client_c = await pool.get_client(
            key_c, headers={}, timeout_ms=1_000, credential_ref=None
        )
        assert not client_a.is_closed, "in-flight client 不得被驱逐"
        assert client_b.is_closed, "idle LRU entry 应被驱逐腾位"
        assert client_c is not None
        assert pool.client_count == 2  # a(在飞) + c
    await pool.close()


async def test_S_A18_eviction_when_all_in_flight_does_not_close_any() -> None:
    """A18：pool 满、所有 entry 均 in_flight>0 时 _evict_for_capacity 本轮不驱逐
    （pool 暂时超 max，优于杀掉在飞 call）。"""
    pool = MCPHTTPClientPool(ttl_seconds=300, max_clients=2)
    key_a = MCPHTTPPoolKey("t", "u", "http://a", "v1", "1")
    key_b = MCPHTTPPoolKey("t", "u", "http://b", "v1", "1")
    client_a = await pool.get_client(
        key_a, headers={}, timeout_ms=1_000, credential_ref=None
    )
    client_b = await pool.get_client(
        key_b, headers={}, timeout_ms=1_000, credential_ref=None
    )
    async with (
        pool.session(key_a, headers={}, timeout_ms=1_000, credential_ref=None) as _a,
        pool.session(key_b, headers={}, timeout_ms=1_000, credential_ref=None) as _b,
    ):
        assert _a is client_a and _b is client_b
        key_c = MCPHTTPPoolKey("t", "u", "http://c", "v1", "1")
        client_c = await pool.get_client(
            key_c, headers={}, timeout_ms=1_000, credential_ref=None
        )
        assert not client_a.is_closed and not client_b.is_closed
        assert client_c is not None
        assert pool.client_count == 3  # 超 max，但不杀在飞
    await pool.close()


async def test_S_A18_version_invalidation_skips_in_flight_old_version() -> None:
    """A18：新版本请求触发 _invalidate_changed_version 时，in_flight>0 的旧版本
    entry 跳过——跨执行 version-invalidation 不得关掉另一执行期正用的旧版本
    client（ADR-005 版本锚定）。in_flight 归零后下一周期才清理。"""
    pool = MCPHTTPClientPool(ttl_seconds=300, max_clients=4)
    key_v1 = MCPHTTPPoolKey("t", "u", "http://mcp", "v1", "1")
    key_v2 = MCPHTTPPoolKey("t", "u", "http://mcp", "v2", "2")
    client_v1 = await pool.get_client(
        key_v1, headers={}, timeout_ms=1_000, credential_ref=None
    )
    # 占住 v1（执行期 A 正用旧版本）。
    async with pool.session(
        key_v1, headers={}, timeout_ms=1_000, credential_ref=None
    ):
        # 执行期 B 请求新版本 v2 → 不得关掉 v1（in_flight=1）。
        client_v2 = await pool.get_client(
            key_v2, headers={}, timeout_ms=1_000, credential_ref=None
        )
        assert not client_v1.is_closed, "in-flight 旧版本 client 不得被 version invalidation 关掉"
        assert client_v2 is not None
        assert pool.client_count == 2  # v1 + v2 共存
    # 退出 session → v1 in_flight=0；再次请求 v2（hit）触发 _invalidate 关掉 v1。
    await pool.get_client(key_v2, headers={}, timeout_ms=1_000, credential_ref=None)
    assert client_v1.is_closed, "in_flight 归零后旧版本 entry 应在下一周期被清理"
    await pool.close()


async def test_S_A18_expire_skips_in_flight_entry() -> None:
    """A18：TTL 到期的 entry 若 in_flight>0 则跳过——不得因 TTL 关掉在用 client；
    in_flight 归零后下一周期 expire。"""
    pool = MCPHTTPClientPool(ttl_seconds=0.01, max_clients=4)
    key = MCPHTTPPoolKey("t", "u", "http://mcp", "v1", "1")
    client = await pool.get_client(
        key, headers={}, timeout_ms=1_000, credential_ref=None
    )
    # 占住 client 并等 TTL 过期——在飞期间不得被 expire 关掉。
    async with pool.session(
        key, headers={}, timeout_ms=1_000, credential_ref=None
    ):
        await asyncio.sleep(0.02)
        # 再次请求同 key（hit）触发 _expire：in_flight=1 → 跳过。
        refreshed = await pool.get_client(
            key, headers={}, timeout_ms=1_000, credential_ref=None
        )
        assert not client.is_closed, "in-flight client 不得因 TTL 过期被关闭"
        assert refreshed is client
    # 退出 session → in_flight=0；再次请求触发 _expire 关掉它。
    await asyncio.sleep(0.02)
    new_client = await pool.get_client(
        key, headers={}, timeout_ms=1_000, credential_ref=None
    )
    assert client.is_closed, "in_flight 归零且 TTL 到期后应被 expire 关闭"
    assert new_client is not client
    await pool.close()


async def test_S_A18_session_increments_and_decrements_in_flight() -> None:
    """A18：session enter inc in_flight、exit dec——计数精确配对，无泄漏。"""
    pool = MCPHTTPClientPool(ttl_seconds=300, max_clients=4)
    key = MCPHTTPPoolKey("t", "u", "http://mcp", "v1", "1")
    await pool.get_client(key, headers={}, timeout_ms=1_000, credential_ref=None)
    entry = pool._entries[key]
    assert entry.in_flight == 0
    async with pool.session(
        key, headers={}, timeout_ms=1_000, credential_ref=None
    ):
        assert entry.in_flight == 1
        async with pool.session(
            key, headers={}, timeout_ms=1_000, credential_ref=None
        ):
            assert entry.in_flight == 2
        assert entry.in_flight == 1
    assert entry.in_flight == 0
    await pool.close()
