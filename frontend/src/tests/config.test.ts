import assert from 'node:assert/strict';
import test from 'node:test';
import {apiBase} from '../lib/config.ts';

test('production requires explicit non-loopback HTTPS without credentials', () => {
  for (const value of [undefined, '', 'http://api.example.test', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://user:pass@api.example.test', 'https://api.example.test/?token=x']) assert.throws(() => apiBase(value, true));
  assert.equal(apiBase('https://api.example.test/', true), 'https://api.example.test');
  assert.equal(apiBase(undefined, false), 'http://127.0.0.1:8000');
});
