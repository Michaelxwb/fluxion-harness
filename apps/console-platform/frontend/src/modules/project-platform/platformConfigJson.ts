/** Preserve arbitrary objects; the backend validates the Adapter's schema. */
export function parsePlatformConfig(text: string | undefined): Record<string, unknown> {
  const value: unknown = JSON.parse(text?.trim() || '{}');
  if (value === null || Array.isArray(value) || typeof value !== 'object') {
    throw new Error('Platform config must be a JSON object');
  }
  return value as Record<string, unknown>;
}

export function platformConfigExample(schema: Record<string, unknown> | undefined): string {
  const properties = schema?.properties as Record<string, Record<string, unknown>> | undefined;
  return JSON.stringify(Object.fromEntries(Object.entries(properties ?? {})
    .filter(([, definition]) => Object.prototype.hasOwnProperty.call(definition, 'default'))
    .map(([key, definition]) => [key, definition.default])), null, 2);
}
