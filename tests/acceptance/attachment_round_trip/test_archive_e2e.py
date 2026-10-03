"""Real model tool call → ZIP storage → WeCom upload → HTTP download."""

from __future__ import annotations

import base64
import io
import zipfile

import httpx
from muad_console_platform.infrastructure.skill_validator import validated_package

from tests.acceptance.attachment_round_trip.test_round_trip_e2e import (
    _args,
    _audit_ids,
    _callback,
    _media_frames,
    _new_audit_rows,
    _new_message,
    _push,
    _set_script,
    _wait_connected,
    _wait_for_reply,
    _wait_run_settled,
)
from tests.acceptance.im_gateway.environment import GatewayStack

FILES = [
    {"path": "SKILL.md", "content": "---\nname: greeting\ndescription: fixed greeting\n---\nHello"},
    {"path": "muad.skill.json", "content": '{"runtime":"script","entrypoint":"scripts/run.mjs"}'},
    {"path": "scripts/run.mjs", "content": 'console.log("你好，见到你很高兴");'},
]


async def _download(stack: GatewayStack, artifact_id: object) -> bytes:
    async with httpx.AsyncClient(timeout=30.0) as client:
        issued = await client.post(
            f"{stack.console_url}/internal/artifacts/{artifact_id}/fetch-link",
            headers=stack.service_headers(),
        )
        assert issued.status_code == 200, issued.text
        response = await client.get(issued.json()["data"]["url"])
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert "greeting.zip" in response.headers["content-disposition"]
        return response.content


async def test_created_archive_is_delivered_and_downloadable(gateway_stack: GatewayStack) -> None:
    stack = gateway_stack
    await _wait_connected(stack)
    probe = stack.ws_probe
    assert probe is not None
    chunks_before = len(probe.upload_chunks)
    media_before = len(_media_frames(stack))
    audits_before = await _audit_ids(stack)
    message_id, reply_id = _new_message("archive")
    await _set_script(
        stack,
        tools=[
            {"name": "create_archive", "arguments": _args(filename="greeting.zip", files=FILES)},
            {"name": "deliver_artifact", "arguments": _args(artifact_id="$last_artifact_id")},
        ],
        final_text="压缩包已生成并发送。",
    )
    await _push(
        stack,
        _callback(
            message_id,
            reply_id,
            {
                "msgtype": "text",
                "text": {"content": "把 greeting 三个文件打包后发给我"},
            },
        ),
    )
    await _wait_for_reply(stack, reply_id, "压缩包已生成并发送")
    await _wait_run_settled(stack)
    media = _media_frames(stack)[media_before:]
    assert len(media) == 1
    assert media[0]["body"]["msgtype"] == "file"
    assert media[0]["body"]["file"]["media_id"]
    audits = await _new_audit_rows(stack, audits_before)
    assert len(audits) == 1
    assert audits[0][1] == "DELIVERED"
    downloaded = await _download(stack, audits[0][0])
    uploaded = b"".join(
        base64.b64decode(frame["body"]["base64_data"]) for frame in probe.upload_chunks[chunks_before:]
    )
    assert uploaded == downloaded
    _assert_skill_archive(downloaded)


def _assert_skill_archive(downloaded: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
        assert archive.namelist() == [item["path"] for item in FILES]
        for item in FILES:
            assert archive.read(item["path"]) == item["content"].encode("utf-8")
    with validated_package(downloaded) as package:
        assert package.default_key == "greeting"
