from collections.abc import Callable, Iterator

import pytest

from tests.acceptance.datastores import create_datastore, drop_datastore
from tests.async_tool_datastores import (
    async_tool_database as async_tool_database,
)
from tests.async_tool_datastores import (
    async_tool_datastore as async_tool_datastore,
)


@pytest.fixture
def datastore_at_revision() -> Iterator[Callable[[str], dict[str, str]]]:
    """按**指定 revision** 建临时库的工厂夹具，收尾统一丢弃。

    单条 revision 的升级/回退用例只应看到"这条 revision 之前/之后"的结构：head 随迁移链
    前进时，拿 head 库去 `downgrade` 会先撞上后续 revision 自带的门禁（那是别的修订的
    契约，不是本套的断言对象）。用法：`info = datastore_at_revision("0026")`。
    """
    created: list[dict[str, str]] = []

    def factory(revision: str) -> dict[str, str]:
        info = create_datastore(revision=revision)
        created.append(info)
        return info

    yield factory

    for info in created:
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])
