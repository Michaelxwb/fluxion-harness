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
const { credentialFields, validateCredentialValues } = context.exports;
const fields = credentialFields({ credential_schema: {
  required: ['ak', 'sk'], properties: { ak: { 'x-secret': true }, sk: { 'x-secret': true } }
}});
test('AK/SK both required and masked; no public config fields', () => {
  assert.equal(fields.map(f => f.name).join(','), 'ak,sk');
  assert.ok(fields.every(f => f.secret && f.required));
  assert.equal(validateCredentialValues(fields, { ak: 'example' }).field, 'sk');
  assert.equal(validateCredentialValues(fields, { sk: 'example' }).field, 'ak');
  assert.equal(validateCredentialValues(fields, { ak: 'example', sk: 'example' }), null);
});
test('generic credential alternatives keep existing semantics', () => {
  const generic = credentialFields({ credential_schema: { properties: {
    token: {}, username: {}, password: {}
  }}});
  assert.equal(validateCredentialValues(generic, { token: 'example' }), null);
  assert.equal(validateCredentialValues(generic, { username: 'example' }).field, 'password');
});
