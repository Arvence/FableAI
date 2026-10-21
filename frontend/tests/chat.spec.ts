import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const base = '/api/v1/conversations';
const sample = 'An original fictional example begins with a brief introduction and closes with practical details about a pretend exhibition.';

test.beforeEach(async ({ page }) => {
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await expect(page.getByLabel('Your instruction')).toBeEnabled();
});

async function send(page: Page, text: string) {
  await page.getByLabel('Your instruction').fill(text);
  const response = page.waitForResponse(value => value.url().endsWith('/messages') && value.request().method() === 'POST');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const result = await response;
  await expect(page.getByLabel('Your instruction')).toBeEnabled();
  await expect(page.getByLabel('Your instruction')).toBeFocused();
  return result;
}

async function addSamples(page: Page) {
  await page.getByText('Add samples, headlines, or URLs', { exact: true }).click();
  await page.getByRole('button', { name: 'Add sample text', exact: true }).click();
  await page.getByLabel('Sample text 1', { exact: true }).fill(sample);
  await page.getByLabel('Attach sample text files (.txt)').setInputFiles({ name: 'sample.txt', mimeType: 'text/plain', buffer: Buffer.from('A second self-authored example describes the format of a completely invented neighborhood workshop.') });
  await expect(page.getByLabel('Sample text 2', { exact: true })).toBeVisible();
  await page.getByLabel('Headlines — one per line').fill('An imaginary lantern workshop opens\nA pretend neighborhood plans an exhibit');
}

test('multi-turn drafts, exact JSON, targeted revisions, reviews, and reload', async ({ page, context }, testInfo) => {
  await addSamples(page);
  await send(page, 'Remember these preferences and examples.');
  const generated = await (await send(page, 'Generate two drafts.')).json();
  expect(generated.kind).toBe('generation');
  await expect(page.getByRole('article', { name: 'Entry 1', exact: true })).toContainText('Synthetic · Fictional');
  const second = page.getByRole('article', { name: 'Entry 2', exact: true });
  await expect(second).toContainText('Clock Orchard');
  const assistant = page.getByRole('article', { name: 'FableAI message', exact: true }).last();
  await assistant.getByText('View response JSON', { exact: true }).click();
  expect(JSON.parse(await assistant.getByLabel('Exact response JSON').innerText())).toEqual(generated);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await assistant.getByRole('button', { name: 'Copy JSON' }).click();
  await expect(assistant.getByRole('status')).toHaveText('JSON copied.');
  expect(JSON.parse(await page.evaluate(() => navigator.clipboard.readText()))).toEqual(generated);
  const first = page.getByRole('article', { name: 'Entry 1', exact: true });
  await first.getByRole('button', { name: 'Accept', exact: true }).click();
  await expect(first.getByText('accepted', { exact: true })).toBeVisible();
  await expect(second.getByRole('button', { name: 'Revise entry 2' })).toBeEnabled();
  await second.getByRole('button', { name: 'Revise entry 2' }).click();
  await expect(page.getByLabel('Your instruction')).toBeFocused();
  const revised = await (await send(page, 'Make entry 2 shorter.')).json();
  expect(revised.kind).toBe('revision');
  expect(revised.entries[0].id).toBe(generated.entries[1].id);
  await expect(second).toContainText('v2');
  await expect(first).toContainText(generated.entries[0].body);
  await expect(first.getByText('accepted', { exact: true })).toBeVisible();
  await second.getByRole('button', { name: 'Reject', exact: true }).click();
  await expect(second.getByText('rejected', { exact: true })).toBeVisible();
  const id = await page.getByLabel('Conversation', { exact: true }).inputValue();
  await page.reload();
  await expect(page.getByLabel('Conversation', { exact: true })).toHaveValue(id);
  await expect(second.getByText('rejected', { exact: true })).toBeVisible();
  await expect(page.getByRole('article', { name: 'You message', exact: true })).toHaveCount(3);
  await expect(page.getByRole('heading', { name: 'Saved references 4/10' })).toBeVisible();
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await expect(page.getByLabel('Your instruction')).toBeEnabled();
  await page.getByLabel('Conversation', { exact: true }).selectOption(id);
  await expect(second).toContainText('v2');
  await expect(page.getByLabel('Your instruction')).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath('desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('mobile-drafts.png'), fullPage: true });
});

test('URLs remain links until an authorized import; blocked imports persist', async ({ page }) => {
  const before = await (await page.request.get('/api/v1/test-state')).json();
  await page.getByText('Add samples, headlines, or URLs', { exact: true }).click();
  await page.getByLabel('Reference URLs — one HTTPS link per line').fill('https://example.com/source\nhttps://127.0.0.1/private');
  await send(page, 'Save these links.');
  await expect(page.getByText('Link only', { exact: true })).toHaveCount(2);
  expect((await (await page.request.get('/api/v1/test-state')).json()).import_requests).toBe(before.import_requests);
  const links = page.locator('.reference-url');
  for (const index of [0, 1]) {
    const link = links.nth(index);
    await link.getByText('Authorize an import', { exact: true }).click();
    await expect(link.getByRole('button', { name: 'Import authorized page' })).toBeDisabled();
    await link.getByLabel('Permission details').fill('Self-authored synthetic test fixture.');
    await link.getByLabel('I confirm automated access is permitted.').check();
    await link.getByLabel('The source may be used in AI prompts.').check();
    await link.getByLabel('The source is non-confidential.').check();
    await link.getByRole('button', { name: 'Import authorized page' }).click();
    await expect(link.getByText(index === 0 ? 'Imported' : 'Blocked / unavailable', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Your instruction')).toBeEnabled();
  }
  expect((await (await page.request.get('/api/v1/test-state')).json()).import_requests).toBe(before.import_requests + 1);
  await page.reload();
  await expect(page.getByText('Imported', { exact: true })).toBeVisible();
  await expect(page.getByText('Blocked / unavailable', { exact: true })).toBeVisible();
  await expect(page.getByText(/Paste an authorized excerpt instead/)).toBeVisible();
});

test('quota and invalid responses are errors, with no automatic resubmission', async ({ page }) => {
  const before = await (await page.request.get('/api/v1/test-state')).json();
  const quota = await send(page, 'Check the quota error.');
  expect(quota.status()).toBe(429);
  await expect(page.getByRole('alert')).toContainText('Gemini quota reached');
  await expect(page.getByLabel('Your instruction')).toHaveValue('Check the quota error.');
  await page.reload();
  await expect(page.getByRole('article', { name: 'FableAI message', exact: true })).toHaveCount(1);
  expect((await (await page.request.get('/api/v1/test-state')).json()).provider_calls).toBe(before.provider_calls + 1);
  expect((await send(page, 'Return an invalid response.')).status()).toBe(502);
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('article', { name: /^Entry \d/ })).toHaveCount(0);
});

test('validation errors preserve input and show backend JSON', async ({ page }) => {
  await page.getByText('Add samples, headlines, or URLs', { exact: true }).click();
  await page.getByLabel('Reference URLs — one HTTPS link per line').fill('http://example.com/source');
  await page.getByLabel('Your instruction').fill('Save links.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('complete HTTPS');
  await expect(page.getByRole('article', { name: 'You message', exact: true })).toHaveCount(0);
  await page.getByLabel('Reference URLs — one HTTPS link per line').fill('');
  await page.getByLabel('Headlines — one per line').fill(Array.from({ length: 10 }, (_, i) => `Invented headline ${i}`).join('\n'));
  expect((await send(page, 'Save these headlines.')).status()).toBe(200);
  await page.getByLabel('Headlines — one per line').fill('An eleventh fictional headline');
  expect((await send(page, 'Save another headline.')).status()).toBe(422);
  await expect(page.getByRole('alert')).toContainText('invalid');
  await expect(page.getByLabel('Headlines — one per line')).toHaveValue('An eleventh fictional headline');
});

test('uncertain submission survives reload and retries the same idempotency key', async ({ page }) => {
  let key: string | undefined;
  let messageId: string | undefined;
  await page.route('**/messages', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    key = route.request().headers()['idempotency-key'];
    const result = await route.fetch();
    messageId = (await result.json()).message_id;
    await route.abort('failed');
  });
  await page.getByLabel('Your instruction').fill('Save a slow preference.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Retry same submission' })).toBeVisible();
  const calls = (await (await page.request.get('/api/v1/test-state')).json()).provider_calls;
  await page.unroute('**/messages');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Retry same submission' })).toBeVisible();
  const replay = page.waitForResponse(value => value.url().endsWith('/messages') && value.request().method() === 'POST');
  await page.getByRole('button', { name: 'Retry same submission' }).click();
  const result = await replay;
  expect(result.request().headers()['idempotency-key']).toBe(key);
  expect((await result.json()).message_id).toBe(messageId);
  await expect(page.getByLabel('Your instruction')).toBeEnabled();
  await expect(page.getByRole('article', { name: 'You message', exact: true })).toHaveCount(1);
  expect((await (await page.request.get('/api/v1/test-state')).json()).provider_calls).toBe(calls);
});

test('mobile layout and keyboard input preserve multiline text and IME composition', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const input = page.getByLabel('Your instruction');
  await input.focus();
  await input.fill('First line');
  await input.press('Shift+Enter');
  await input.pressSequentially('Second line');
  await expect(input).toHaveValue('First line\nSecond line');
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
  await expect(page.getByRole('article', { name: 'You message', exact: true })).toHaveCount(0);
  await input.press('Enter');
  await expect(page.getByRole('article', { name: 'You message', exact: true })).toContainText('First line\nSecond line');
  await expect(input).toHaveValue('');
  await input.fill('Unsent draft survives reload');
  await page.reload();
  await expect(input).toHaveValue('Unsent draft survives reload');
  await page.getByRole('button', { name: 'Refresh', exact: true }).focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('region', { name: 'Message history', exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByLabel('Conversation', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Message composer')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('mobile.png'), fullPage: true });
});

test('local proxy accepts only the intended browser origins', async ({ request }) => {
  expect((await request.post(base, { headers: { Origin: 'http://127.0.0.1:5173' } })).status()).toBe(201);
  expect((await request.post(base, { headers: { Origin: 'https://untrusted.example' } })).status()).toBe(403);
});
