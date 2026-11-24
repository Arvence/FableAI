import assert from 'node:assert/strict';
import test from 'node:test';
import { generate } from './generate.mjs';

const apiKey = 'fableai_' + 'a'.repeat(32) + '_' + 'b'.repeat(43);
const input = { apiKey, projectId: 'sample-project', instruction: 'An imaginary harbor festival.' };

test('the example sends one scoped generation request with a matching response ID', async context => {
  const entries = [{ title: '[FICTIONAL] Harbor Festival', body: 'A fictional draft.' }];
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return Response.json({ entries }, { headers: { 'Idempotency-Key': options.headers['Idempotency-Key'] } });
  });
  assert.deepEqual(await generate(input), { entries });
  assert.equal(calls.length, 1);
  const { url, options } = calls[0];
  assert.equal(url.href, 'http://127.0.0.1:8000/api/v1/projects/sample-project/generations');
  assert.equal(options.headers.Authorization, 'Bearer ' + apiKey);
  assert.match(options.headers['Idempotency-Key'], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.deepEqual(JSON.parse(options.body), { instruction: input.instruction });
  assert.equal(options.redirect, 'error');
});

test('invalid inputs and insecure remote origins fail before making a request', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => { throw new Error('Unexpected request.'); });
  for (const changes of [{ baseUrl: 'http://example.com' }, { baseUrl: 'https://user:password@example.com' }, { apiKey: 'invalid' }, { projectId: '../other' }, { instruction: '' }, { instruction: 'x'.repeat(4001) }]) {
    await assert.rejects(generate({ ...input, ...changes }));
  }
  assert.equal(fetch.mock.callCount(), 0);
});

for (const failure of ['quota', 'server', 'network', 'mismatched-id', 'empty', 'invalid-json']) {
  test('the example reports ' + failure + ' without retrying or exposing credentials', async context => {
    const fetch = context.mock.method(globalThis, 'fetch', async (_url, options) => {
      if (failure === 'network') throw new TypeError('Offline connection failure.');
      if (failure === 'quota' || failure === 'server') return Response.json({ error: 'failure' }, { status: failure === 'quota' ? 429 : 503 });
      const headers = { 'Idempotency-Key': failure === 'mismatched-id' ? 'different-request' : options.headers['Idempotency-Key'] };
      if (failure === 'invalid-json') return new Response('invalid', { headers });
      return Response.json({ entries: failure === 'empty' ? [] : [{}] }, { headers });
    });
    await assert.rejects(generate(input), error => !error.message.includes(apiKey));
    assert.equal(fetch.mock.callCount(), 1);
  });
}
