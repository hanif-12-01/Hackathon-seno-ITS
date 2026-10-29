import assert from 'node:assert/strict';
import test from 'node:test';
import {apiBase} from '../lib/config.ts';

test('production requires explicit HTTPS without credentials, including local TLS gateways', () => {
  for (const value of [undefined, '', 'http://api.example.test', 'http://localhost:8443/api', 'https://user:pass@api.example.test', 'https://api.example.test/?token=x']) assert.throws(() => apiBase(value, true));
  for (const value of ['https://localhost:8443/api', 'https://127.0.0.1:8443/api', 'https://[::1]:8443/api']) assert.equal(apiBase(value, true), value);
  assert.equal(apiBase('https://api.example.test/', true), 'https://api.example.test');
  assert.equal(apiBase(undefined, false), 'http://127.0.0.1:8000');
});
