"""模型探针的 `/script` 契约：坏脚本当场 400，不要等到模型调用时才 500。

`delay_ms` 由白名单按键取值，不校验形状；探针的脚本又是**模块级全局**（跨用例留存），
所以一旦写进一个非数值，后续每个用例的模型调用都会炸在 `int()` 上——那时看起来像模型侧挂了。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from tests.e2e.openai_probe_app import app


@pytest.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as instance:
        try:
            yield instance
        finally:
            await instance.post("/script", json={})  # 脚本是模块级全局：用完必须清


async def test_script_rejects_a_delay_that_is_not_a_non_negative_integer(
    client: httpx.AsyncClient,
) -> None:
    for payload in ({"delay_ms": None}, {"delay_ms": "1500"}, {"delay_ms": -1}):
        response = await client.post("/script", json=payload)
        assert response.status_code == 400, (payload, response.text)

    accepted = await client.post("/script", json={"delay_ms": 1500})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["script"]["delay_ms"] == 1500
