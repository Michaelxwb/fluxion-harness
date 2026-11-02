const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(new URL('../src/modules/project-platform/CredentialSchemaFields.tsx', `file://${__filename}`), 'utf8');
// Execute the real pure validation exports; UI rendering is covered by build/browser checks.
const context = { exports: {}, require: () => ({}) };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX }
}).outputText, context);
const { parseCredentialJson } = context.exports;
test('custom credentials preserve nested objects, arrays and non-string values', () => {
  const value = { custom_auth: { key: 'example', enabled: true }, scopes: ['read'], ttl: 90 };
  assert.equal(JSON.stringify(parseCredentialJson(JSON.stringify(value))), JSON.stringify(value));
  assert.equal(JSON.stringify(parseCredentialJson('{}')), '{}');
});
test('reject malformed JSON and non-object roots', () => {
  for (const input of ['', '{', 'null', '[]', '"token"', '42', 'true']) {
    assert.throws(() => parseCredentialJson(input));
  }
});
