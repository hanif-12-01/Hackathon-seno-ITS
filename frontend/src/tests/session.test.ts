import {test} from 'node:test';
import assert from 'node:assert/strict';
import {clearCredentials, request, setCredentials} from '../lib/api.ts';
import {Operations} from '../lib/operations.ts';

test('principal journals isolate results, preserve uncertain recovery, reject ownerless pending journal', async () => {
  const rows = new Map<string, string>();
  const storage = {getItem: (key: string) => rows.get(key) ?? null, setItem: (key: string, value: string) => {rows.set(key, value);}};
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {calls++; return new Response('{"owner":"alice"}');};
  try {
    const alice = new Operations('/api', storage, 'alice');
    await alice.execute('/cases', {created_by: 'alice'});
    assert.equal(new Operations('/api', storage, 'bob').blocked(), false);
    globalThis.fetch = async () => {calls++; throw new TypeError('response lost');};
    await assert.rejects(alice.execute('/cases/one/replan', {}), /belum diketahui/);
    assert.equal(new Operations('/api', storage, 'alice').blocked(), true);
    assert.equal(new Operations('/api', storage, 'bob').blocked(), false);
    assert.equal(await new Operations('/api', storage, 'bob').reconcile(), true);
    assert.equal(calls, 2, 'Bob never reads/replays Alice operation');
    assert.equal(await new Operations('/api', storage, 'alice').execute('/cases', {created_by: 'alice'}).catch(() => 'blocked'), 'blocked');
    rows.set('pangannadi.operations.v1:/api', '[{"state":"uncertain"}]');
    assert.throws(() => new Operations('/api', storage, 'bob'), /tanpa identitas/);
  } finally {globalThis.fetch = original;}
});

test('late old-session response cannot clear a new principal or return private data', async () => {
  const original = globalThis.fetch;
  Object.defineProperty(globalThis, 'location', {configurable: true, value: new URL('https://stage.example.test/')});
  const resolvers: ((response: Response) => void)[] = [];
  globalThis.fetch = () => new Promise(resolve => resolvers.push(resolve));
  try {
    setCredentials('alice', 'test-only');
    const oldData = request('/api', '/cases');
    const old401 = request('/api', '/session');
    clearCredentials();
    setCredentials('bob', 'test-only');
    resolvers[0](new Response('[{"private":"alice"}]'));
    resolvers[1](new Response('{}', {status: 401}));
    await assert.rejects(oldData, /Sesi berubah/);
    await assert.rejects(old401, /Sesi berubah/);
    globalThis.fetch = async (_url, init) => {
      assert.equal((init?.headers as Record<string,string>).Authorization, 'Basic ' + btoa('bob:test-only'));
      return new Response('{}');
    };
    await request('/api', '/session');
  } finally {clearCredentials(); globalThis.fetch = original;}
});
