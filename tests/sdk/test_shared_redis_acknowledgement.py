"""Validate replication replies before accepting a metering operation."""
from unittest.mock import AsyncMock

import pytest
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedRedisMeter

pytestmark = [pytest.mark.unit, pytest.mark.no_datastore]


@pytest.mark.parametrize("reply", [None, True, "1", b"1", [], 0])
async def test_invalid_or_missing_replica_acknowledgement_fails_closed(reply):
    redis = AsyncMock()
    redis.execute_command.side_effect = [["OK"], "redis_mode:cluster\r\n", reply]
    meter = SharedRedisMeter(redis, domain="test", epoch="epoch")
    with pytest.raises(MeteringUnavailable, match="acknowledgement|not replicated"):
        await meter._eval("return {'OK'}", [meter._key("guard")], [])


@pytest.mark.parametrize("code", ["OK", b"OK", "DUPLICATE"])
async def test_integer_replica_acknowledgement_accepts_success(code):
    redis = AsyncMock()
    redis.execute_command.side_effect = [[code], "redis_mode:cluster\r\n", 1]
    meter = SharedRedisMeter(redis, domain="test", epoch="epoch")
    result = await meter._eval("return {'OK'}", [meter._key("guard")], [])
    assert result == [code.decode() if isinstance(code, bytes) else code]
