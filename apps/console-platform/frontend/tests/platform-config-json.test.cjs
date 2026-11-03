const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(new URL('../src/modules/project-platform/platformConfigJson.ts', `file://${__filename}`), 'utf8');
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
const { parsePlatformConfig, platformConfigExample } = context.exports;
test('nested objects, arrays and booleans survive parsing', () => {
  const input = { future_parameter: { enabled: false, headers: ['a','b'] }, timeout: 0 };
  assert.equal(JSON.stringify(parsePlatformConfig(JSON.stringify(input))), JSON.stringify(input));
});
test('only JSON objects are accepted', () => {
  for (const text of ['null', '[]', 'true', '123', '"text"', '{bad']) {
    assert.throws(() => parsePlatformConfig(text));
  }
  assert.equal(JSON.stringify(parsePlatformConfig('')), '{}');
});
test('examples are derived from schema defaults, not hardcoded platform fields', () => {
  const schema = { properties: { enabled: { default: false }, timeout: { default: 0 }, extra: { default: { foo: 1 } }, requiredOnly: { type: 'string' } } };
  assert.equal(platformConfigExample(schema), JSON.stringify({ enabled: false, timeout: 0, extra: { foo: 1 } }, null, 2));
});
