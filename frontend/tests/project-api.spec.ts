import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const execute = promisify(execFile);
const base = 'http://127.0.0.1:8000/api/v1/projects';

test('documented project consumer uses scoped credentials, saved settings, overrides and replay over HTTP', async ({ request }) => {
  const admin = { Authorization: 'Bearer ' + process.env.FABLEAI_TEST_ADMIN_KEY };
  const id = 'consumer-' + crypto.randomUUID();
  const project = { project_id: id, display_name: 'Example consumer', generation_settings: { count: 1, category: 'news', language: 'en', min_body_chars: 100, max_body_chars: 300 }, instructions: ['Use short paragraphs.'] };
  const created = await request.post(base, { headers: admin, data: project });
  expect(created.status()).toBe(201);
  const issued = await request.post(base + '/' + id + '/keys', { headers: admin, data: { name: 'HTTP smoke consumer' } });
  expect(issued.status()).toBe(201);
  const credential = await issued.json();
  const scoped = { Authorization: 'Bearer ' + credential.api_key };
  expect((await request.get(base + '/' + id + '/output-contract', { headers: scoped })).status()).toBe(200);
  expect((await request.get(base, { headers: scoped })).status()).toBe(403);
  expect((await request.get(base + '/another-project', { headers: scoped })).status()).toBe(403);
  const patched = await request.patch(base + '/' + id, { headers: scoped, data: { generation_settings: { tone: 'informative' } } });
  expect(patched.status()).toBe(200);
  expect((await patched.json()).generation_settings.category).toBe('news');
  const example = fileURLToPath(new URL('../../examples/generate.mjs', import.meta.url));
  const result = await execute(process.execPath, [example, id, 'An imaginary harbor festival'], { env: { ...process.env, FABLEAI_API_URL: 'http://127.0.0.1:8000', FABLEAI_API_KEY: credential.api_key } });
  const content = JSON.parse(result.stdout);
  expect(content.entries).toHaveLength(1);
  expect(content.entries[0]).toMatchObject({ category: 'news', language: 'en', is_synthetic: true, status: 'draft' });
  expect(content.entries[0].title).toMatch(/^\[FICTIONAL\] /);
  const headers = { ...scoped, 'Idempotency-Key': crypto.randomUUID() };
  const body = { instruction: 'An imaginary harbor festival', overrides: { category: 'announcement', count: 2 } };
  const response = await request.post(base + '/' + id + '/generations', { headers, data: body });
  expect(response.status()).toBe(200);
  const drafts = await response.json();
  expect(drafts.entries).toHaveLength(2);
  expect(drafts.entries.every((entry: { category: string }) => entry.category === 'announcement')).toBe(true);
  const replay = await request.post(base + '/' + id + '/generations', { headers, data: body });
  expect(await replay.json()).toEqual(drafts);
  expect(replay.headers()['idempotency-replayed']).toBe('true');
  expect((await request.get(base + '/' + id, { headers: scoped })).status()).toBe(200);
  const unchanged = await (await request.get(base + '/' + id, { headers: scoped })).json();
  expect(unchanged.generation_settings.count).toBe(1);
  expect(unchanged.generation_settings.category).toBe('news');
  expect((await request.post(base + '/' + id + '/generations', { headers: scoped, data: { instruction: '' } })).status()).toBe(422);
  expect((await request.delete(base + '/' + id + '/keys/' + credential.credential_id, { headers: admin })).status()).toBe(204);
  expect((await request.post(base + '/' + id + '/generations', { headers, data: body })).status()).toBe(401);
});

test('invalid persisted composer data cannot crash a restored conversation', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await expect(page.getByLabel('Your message')).toBeEnabled();
  const id = await page.locator('.chat-link[aria-current="page"]').getAttribute('data-conversation-id');
  for (const value of [null, 17, [], {}, { instruction: 'Keep the message.', attachments: [null, 1, {}] }, { instruction: 'Keep the message.', attachments: null }]) {
    await page.evaluate(({ id, value }) => sessionStorage.setItem('fableai:draft:' + id, JSON.stringify(value)), { id, value });
    await page.reload();
    await expect(page.getByLabel('Your message')).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible();
    await expect(page.getByRole('list', { name: 'Attached files' })).toHaveCount(0);
  }
});
