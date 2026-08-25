import type { JsonRecord } from "../types/console";

export function parseSpec(value: string): JsonRecord {
  const parsed: unknown = JSON.parse(value);
  if (isJsonRecord(parsed)) {
    return parsed;
  }
  throw new Error("Spec 必须是 JSON Object");
}

export function isJsonRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
