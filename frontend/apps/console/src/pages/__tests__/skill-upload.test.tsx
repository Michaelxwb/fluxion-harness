import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/** 最小 stored-zip 构造器（测试专用：无压缩，仅 local header + central directory）。 */
function zipFile(name: string, files: Record<string, string>): File {
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  const pushU32 = (bytes: number[], value: number): void => {
    bytes.push(value & 0xff, (value >> 8) & 0xff, (value >> 16) & 0xff, (value >> 24) & 0xff);
  };
  for (const [entryName, text] of Object.entries(files)) {
    const nameBytes = encoder.encode(entryName);
    const data = encoder.encode(text);
    const crc = crc32(data);
    const local: number[] = [];
    pushU32(local, 0x04034b50);
    local.push(20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    pushU32(local, crc);
    pushU32(local, data.length);
    pushU32(local, data.length);
    local.push(nameBytes.length & 0xff, (nameBytes.length >> 8) & 0xff, 0, 0);
    const header = new Uint8Array([...local, ...nameBytes]);
    chunks.push(header, data);
    const record: number[] = [];
    pushU32(record, 0x02014b50);
    record.push(20, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    pushU32(record, crc);
    pushU32(record, data.length);
    pushU32(record, data.length);
    record.push(nameBytes.length & 0xff, (nameBytes.length >> 8) & 0xff, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    pushU32(record, offset);
    central.push(new Uint8Array([...record, ...nameBytes]));
    offset += header.length + data.length;
  }
  const centralStart = offset;
  let centralSize = 0;
  for (const part of central) {
    chunks.push(part);
    centralSize += part.length;
  }
  const end: number[] = [];
  pushU32(end, 0x06054b50);
  end.push(0, 0, 0, 0);
  const count = Object.keys(files).length;
  end.push(count & 0xff, (count >> 8) & 0xff, count & 0xff, (count >> 8) & 0xff);
  pushU32(end, centralSize);
  pushU32(end, centralStart);
  end.push(0, 0);
  chunks.push(new Uint8Array(end));
  return new File(chunks as unknown as BlobPart[], name, { type: "application/zip" });
}

/** TASK-012：Skill Package 上传交互（ZIP 上传 + 解析预览 + Draft 保存）。 */
describe("skill package upload", () => {
  it("S-F01: upload valid zip → success and draft row appears", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities" });
    await user.click(screen.getByRole("button", { name: "新建 Skill" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Skill Package（.zip）");
    const pkg = zipFile("helper.zip", {
      "manifest.yaml": "name: helper\nversion: '1'\nknowledge: []\n",
      "SKILL.md": "# Helper\nBe helpful."
    });
    await user.click(input);
    fireEvent.change(input, { target: { files: [pkg] } });
    await within(dialog).findByText(/解析成功/);
    const okButton = within(dialog).getByText("上传发布").closest("button");
    expect(okButton).not.toBeNull();
    await user.click(okButton as HTMLElement);
    const list = await screen.findByLabelText("技能列表");
    const matches = await within(list).findAllByText("helper");
    expect(matches.length).toBeGreaterThanOrEqual(1);
    await within(list).findByText("已发布");
  });

  it("E-F01: invalid file → field error, no draft created", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities" });
    await user.click(screen.getByRole("button", { name: "新建 Skill" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Skill Package（.zip）");
    const bad = new File(["not a zip"], "evil.txt", { type: "text/plain" });
    await user.click(input);
    fireEvent.change(input, { target: { files: [bad] } });
    await within(dialog).findByText(/不是有效的 Skill Package|格式错误/);
    const list = await screen.findByLabelText("技能列表");
    expect(within(list).queryByText("evil")).toBeNull();
  });
});
