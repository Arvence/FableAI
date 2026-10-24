import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const base = '/api/v1/conversations';
const sample = 'An original fictional example begins with a brief introduction and closes with practical details about a pretend exhibition.';

test.beforeEach(async ({ page }) => {
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await expect(page.getByLabel('Your message')).toBeEnabled();
});

async function send(page: Page, text: string) {
  await page.getByLabel('Your message').fill(text);
  const response = page.waitForResponse(value => value.url().endsWith('/messages') && value.request().method() === 'POST');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const result = await response;
  await expect(page.getByLabel('Your message')).toBeEnabled();
  await expect(page.getByLabel('Your message')).toBeFocused();
  return result;
}

async function addSamples(page: Page) {
  await page.locator('input[type="file"]').setInputFiles([
    { name: 'sample.txt', mimeType: 'text/plain', buffer: Buffer.from(sample) },
    { name: 'format.txt', mimeType: 'text/plain', buffer: Buffer.from('A second self-authored example describes the format of a completely invented neighborhood workshop.') },
    { name: 'lanterns.txt', mimeType: 'text/plain', buffer: Buffer.from('An imaginary lantern workshop opens') },
    { name: 'exhibit.txt', mimeType: 'text/plain', buffer: Buffer.from('A pretend neighborhood plans an exhibit') },
  ]);
  await expect(page.getByRole('list', { name: 'Attached files' }).getByRole('listitem')).toHaveCount(4);
}

async function selectedChatId(page: Page) {
  return (await page.locator('.chat-link[aria-current="page"]').getAttribute('data-conversation-id'))!;
}

test('multi-turn drafts, exact JSON, chat selection, and reload', async ({ page, context }, testInfo) => {
  await addSamples(page);
  await send(page, 'Remember these preferences and examples.');
  const generated = await (await send(page, 'Generate two drafts.')).json();
  expect(generated.kind).toBe('generation');
  expect(generated.entries).toHaveLength(2);
  expect(generated.entries.every((entry: { is_synthetic: boolean; status: string }) => entry.is_synthetic && entry.status === 'draft')).toBe(true);
  const assistant = page.getByRole('article', { name: 'FableAI message', exact: true }).last();
  await expect(assistant.getByLabel('Exact response JSON')).toBeVisible();
  expect(JSON.parse(await assistant.getByLabel('Exact response JSON').innerText())).toEqual(generated);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await assistant.getByRole('button', { name: 'Copy JSON' }).click();
  await expect(assistant.getByRole('status')).toHaveText('JSON copied.');
  expect(JSON.parse(await page.evaluate(() => navigator.clipboard.readText()))).toEqual(generated);
  const id = await selectedChatId(page);
  await page.reload();
  await expect(page.locator('.chat-link[aria-current="page"]')).toHaveAttribute('data-conversation-id', id);
  await expect(page.getByRole('article', { name: 'You message', exact: true })).toHaveCount(2);
  expect(JSON.parse(await assistant.getByLabel('Exact response JSON').innerText())).toEqual(generated);
  await page.getByLabel('Your message').fill('Keep this unsent draft.');
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  await expect(page.getByLabel('Your message')).toBeEnabled();
  await expect(page.getByLabel('Your message')).toHaveValue('');
  await page.locator('.chat-link[data-conversation-id="' + id + '"]').click();
  await expect(page.getByLabel('Your message')).toHaveValue('Keep this unsent draft.');
  expect(JSON.parse(await assistant.getByLabel('Exact response JSON').innerText())).toEqual(generated);
  await page.screenshot({ path: testInfo.outputPath('desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('mobile-drafts.png'), fullPage: true });
});

test('URL references remain links and are preserved across reload', async ({ page }) => {
  const before = await (await page.request.get('/api/v1/test-state')).json();
  await send(page, 'Save these links: https://example.com/source and https://127.0.0.1/private');
  const id = await selectedChatId(page);
  const saved = await (await page.request.get(base + '/' + id)).json();
  expect(saved.references.urls).toHaveLength(2);
  expect(saved.references.urls.every((link: { import_status: string }) => link.import_status === 'not_requested')).toBe(true);
  expect((await (await page.request.get('/api/v1/test-state')).json()).import_requests).toBe(before.import_requests);
  await page.reload();
  await page.getByText('Submitted references', { exact: true }).click();
  await expect(page.getByRole('article', { name: 'You message', exact: true }).locator('.url-text')).toHaveCount(2);
  expect((await (await page.request.get('/api/v1/test-state')).json()).import_requests).toBe(before.import_requests);
});

test('quota and invalid responses are errors, with no automatic resubmission', async ({ page }) => {
  const before = await (await page.request.get('/api/v1/test-state')).json();
  const quota = await send(page, 'Check the quota error.');
  expect(quota.status()).toBe(429);
  await expect(page.getByRole('alert')).toContainText('Gemini quota reached');
  await expect(page.getByLabel('Your message')).toHaveValue('Check the quota error.');
  await page.reload();
  await expect(page.getByRole('article', { name: 'FableAI message', exact: true })).toHaveCount(1);
  expect((await (await page.request.get('/api/v1/test-state')).json()).provider_calls).toBe(before.provider_calls + 1);
  expect((await send(page, 'Return an invalid response.')).status()).toBe(502);
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('article', { name: /^Entry \d/ })).toHaveCount(0);
});

test('validation errors preserve input and show backend JSON', async ({ page }) => {
  const input = page.getByLabel('Your message');
  await input.fill('Save http://example.com/source');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('complete HTTPS');
  await expect(page.getByRole('article', { name: 'You message', exact: true })).toHaveCount(0);
  await page.locator('input[type="file"]').setInputFiles(Array.from({ length: 10 }, (_, i) => ({
    name: 'sample-' + i + '.txt', mimeType: 'text/plain', buffer: Buffer.from('Invented sample number ' + i),
  })));
  expect((await send(page, 'Save these samples.')).status()).toBe(200);
  await page.locator('input[type="file"]').setInputFiles({ name: 'extra.txt', mimeType: 'text/plain', buffer: Buffer.from('An eleventh fictional sample') });
  expect((await send(page, 'Save another sample.')).status()).toBe(422);
  await expect(page.getByRole('alert')).toContainText('invalid');
  await expect(page.getByRole('list', { name: 'Attached files' })).toContainText('extra');
  await expect(input).toHaveValue('Save another sample.');
});

for (const failure of ['connection loss', 'internal error'] as const) {
  test(`uncertain submission after ${failure} retries the same idempotency key after reload`, async ({ page }) => {
    let key: string | undefined;
    let messageId: string | undefined;
    await page.route('**/messages', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      key = route.request().headers()['idempotency-key'];
      const result = await route.fetch();
      messageId = (await result.json()).message_id;
      if (failure === 'connection loss') {
        await route.abort('failed');
      } else {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ kind: 'error', conversation_id: null, message_id: null, error: { code: 'internal_error', message: 'The request could not be completed.', details: [] } }) });
      }
    });
    await page.getByLabel('Your message').fill('Save a slow preference.');
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
    await expect(page.getByLabel('Your message')).toBeEnabled();
    await expect(page.getByRole('article', { name: 'You message', exact: true })).toHaveCount(1);
    expect((await (await page.request.get('/api/v1/test-state')).json()).provider_calls).toBe(calls);
  });
}


test('mobile layout and keyboard input preserve multiline text and IME composition', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const input = page.getByLabel('Your message');
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
  await page.getByRole('button', { name: 'New chat', exact: true }).focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Delete chat', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Delete this chat?' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Delete chat', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('navigation', { name: 'Chats', exact: true }).getByRole('button').first()).toBeFocused();
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /sidebar/i })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByLabel('Message composer')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('mobile.png'), fullPage: true });
});

test('local proxy accepts only the intended browser origins', async ({ request }) => {
  expect((await request.post(base, { headers: { Origin: 'http://127.0.0.1:5173' } })).status()).toBe(201);
  expect((await request.post(base, { headers: { Origin: 'https://untrusted.example' } })).status()).toBe(403);
});

test('untrusted message markup is displayed as text without browser side effects', async ({ page }) => {
  const markup = '<img src=x onerror="document.body.dataset.untrustedExecuted=1">';
  const response = await send(page, markup);
  expect(response.status()).toBe(200);
  const message = page.getByRole('article', { name: 'You message', exact: true });
  await expect(message).toContainText(markup);
  await expect(message.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => document.body.dataset.untrustedExecuted)).toBeUndefined();
});


test('chat deletion requires confirmation and clears only the selected chat', async ({ page }) => {
  const selector = page.locator('.chat-link[aria-current="page"]');
  const id = await selectedChatId(page);
  await send(page, 'Keep this fictional preference.');
  await page.getByLabel('Your message').fill('An unsent draft to discard');
  const other = await (await page.request.post(base)).json();
  await page.reload();
  const remove = page.getByRole('button', { name: 'Delete chat', exact: true });
  await expect(remove).toBeEnabled();
  await remove.click();
  const dialog = page.getByRole('dialog', { name: 'Delete this chat?' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).not.toBeVisible();
  expect((await page.request.get(base + '/' + id)).status()).toBe(200);
  await expect(page.getByLabel('Your message')).toHaveValue('An unsent draft to discard');
  await remove.click();
  const deleted = page.waitForResponse(response => response.request().method() === 'DELETE' && response.url().endsWith('/' + id));
  await dialog.getByRole('button', { name: 'Delete chat', exact: true }).click();
  expect((await deleted).status()).toBe(204);
  await expect(selector).toHaveAttribute('data-conversation-id', other.conversation_id);
  await expect(page.locator('.chat-link[data-conversation-id="' + id + '"]')).toHaveCount(0);
  expect((await page.request.get(base + '/' + id)).status()).toBe(404);
  expect((await page.request.get(base + '/' + other.conversation_id)).status()).toBe(200);
  expect(await page.evaluate(id => sessionStorage.getItem('fableai:draft:' + id), id)).toBeNull();
  expect(await page.evaluate(id => sessionStorage.getItem('fableai:pending:' + id), id)).toBeNull();
  await page.reload();
  await expect(selector).toHaveAttribute('data-conversation-id', other.conversation_id);
  await expect(page.locator('.chat-link[data-conversation-id="' + id + '"]')).toHaveCount(0);
});

test('deleting the last chat shows the empty workspace and clears the saved selection', async ({ page }) => {
  const selector = page.locator('.chat-link[aria-current="page"]');
  const id = await selectedChatId(page);
  const listing = await (await page.request.get(base + '?limit=100')).json();
  for (const item of listing.items) {
    if (item.conversation_id !== id) expect((await page.request.delete(base + '/' + item.conversation_id)).status()).toBe(204);
  }
  await page.reload();
  await expect(page.getByRole('button', { name: 'Delete chat', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Delete chat', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete chat', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Start a conversation' })).toBeVisible();
  await expect(selector).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Delete chat', exact: true })).toBeDisabled();
  expect(await page.evaluate(() => sessionStorage.getItem('fableai:selected'))).toBeNull();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Start a conversation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled();
});

test('a failed delete preserves the chat and an uncertain successful delete can be retried', async ({ page }) => {
  const selector = page.locator('.chat-link[aria-current="page"]');
  const id = await selectedChatId(page);
  await page.getByLabel('Your message').fill('Keep this draft until deletion succeeds');
  await page.route('**/conversations/' + id, async route => {
    if (route.request().method() !== 'DELETE') return route.continue();
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ kind: 'error', error: { code: 'storage_failed', message: 'Local storage is unavailable.', details: [] } }) });
  });
  await page.getByRole('button', { name: 'Delete chat', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete chat', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Local storage is unavailable.');
  await expect(selector).toHaveAttribute('data-conversation-id', id);
  await expect(page.getByLabel('Your message')).toHaveValue('Keep this draft until deletion succeeds');
  expect((await page.request.get(base + '/' + id)).status()).toBe(200);
  await page.unroute('**/conversations/' + id);
  await page.route('**/conversations/' + id, async route => {
    if (route.request().method() !== 'DELETE') return route.continue();
    await route.fetch();
    await route.abort('failed');
  });
  await page.getByRole('button', { name: 'Delete chat', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete chat', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Cannot reach the local backend.');
  await expect(selector).toHaveAttribute('data-conversation-id', id);
  await page.unroute('**/conversations/' + id);
  await page.getByRole('button', { name: 'Delete chat', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete chat', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Start a conversation' })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(await page.evaluate(id => sessionStorage.getItem('fableai:draft:' + id), id)).toBeNull();
});


test('one message box keeps removable file tiles above it and saves inline links without importing', async ({ page }, testInfo) => {
  const composer = page.getByRole('form', { name: 'Message composer' });
  const input = page.getByLabel('Your message');
  const fileInput = page.locator('input[type="file"]');
  await expect(composer.getByRole('textbox')).toHaveCount(1);
  await expect(input).toHaveAttribute('rows', '1');
  expect(await input.evaluate(element => getComputedStyle(element).resize)).toBe('none');
  const inputBox = (await input.boundingBox())!;
  const attachBox = (await composer.getByRole('button', { name: 'Attach text files', exact: true }).boundingBox())!;
  const sendBox = (await composer.getByRole('button', { name: 'Send', exact: true }).boundingBox())!;
  expect(attachBox.x + attachBox.width).toBeLessThan(inputBox.x);
  expect(sendBox.x).toBeGreaterThan(inputBox.x + inputBox.width);
  expect(Math.abs(attachBox.y + attachBox.height / 2 - inputBox.y - inputBox.height / 2)).toBeLessThan(1);
  expect(Math.abs(sendBox.y + sendBox.height / 2 - inputBox.y - inputBox.height / 2)).toBeLessThan(1);

  const picker = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Attach text files', exact: true }).click();
  await (await picker).setFiles([
    { name: 'town-report.txt', mimeType: 'text/plain', buffer: Buffer.from(sample) },
    { name: 'discard.txt', mimeType: 'text/plain', buffer: Buffer.from('Remove this file before sending.') },
  ]);
  const files = page.getByRole('list', { name: 'Attached files' });
  await expect(files.getByRole('listitem')).toHaveCount(2);
  await expect(files).toContainText('town-report');
  await expect(files).toContainText('.TXT');
  await files.getByRole('button', { name: 'Remove discard.txt' }).click();
  await expect(files.getByRole('listitem')).toHaveCount(1);
  await input.fill('Keep this sample and https://example.com/source.');
  await page.reload();
  await expect(files.getByRole('listitem')).toHaveCount(1);
  await expect(input).toHaveValue('Keep this sample and https://example.com/source.');
  await fileInput.setInputFiles({ name: 'workshop.txt', mimeType: 'text/plain', buffer: Buffer.from('A pretend workshop hosts an imaginary paper sculpture display.') });
  await expect(files.getByRole('listitem')).toHaveCount(2);
  const tiles = await files.getByRole('listitem').evaluateAll(items => items.map(item => ({ top: item.getBoundingClientRect().top, bottom: item.getBoundingClientRect().bottom })));
  expect(tiles[0].top).toBe(tiles[1].top);
  expect(tiles[0].bottom).toBeLessThan((await input.boundingBox())!.y);
  await page.screenshot({ path: testInfo.outputPath('composer-files-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await composer.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('composer-files-mobile.png'), fullPage: true });
  const before = await (await page.request.get('/api/v1/test-state')).json();
  const response = await send(page, 'Remember these samples and (https://example.com/source). Also see https://example.com/demo_(one). Repeated: https://example.com/source');
  const submitted = response.request().postDataJSON();
  expect(submitted.examples).toEqual([sample, 'A pretend workshop hosts an imaginary paper sculpture display.']);
  expect(submitted.source_urls).toEqual(['https://example.com/source', 'https://example.com/demo_(one)']);
  await expect(files).toHaveCount(0);
  expect((await (await page.request.get('/api/v1/test-state')).json()).import_requests).toBe(before.import_requests);
});

test('invalid attachments preserve the message and older saved composer fields migrate', async ({ page }) => {
  const input = page.getByLabel('Your message');
  const fileInput = page.locator('input[type="file"]');
  await input.fill('Keep this message.');
  await fileInput.setInputFiles({ name: 'invalid.pdf', mimeType: 'application/pdf', buffer: Buffer.from('Not a text file.') });
  await expect(page.getByRole('alert')).toContainText('plain-text .txt files');
  await expect(input).toHaveValue('Keep this message.');
  await fileInput.setInputFiles({ name: 'oversized.txt', mimeType: 'text/plain', buffer: Buffer.from('a'.repeat(10001)) });
  await expect(page.getByRole('alert')).toContainText('10,000');
  await expect(page.getByRole('list', { name: 'Attached files' })).toHaveCount(0);
  const id = await selectedChatId(page);
  await page.evaluate(({ id, sample }) => sessionStorage.setItem('fableai:draft:' + id, JSON.stringify({ instruction: 'Remember my saved draft.', examples: [sample], headlines: 'A fictional headline', urls: 'https://example.com/saved' })), { id, sample });
  await page.reload();
  await expect(input).toHaveValue('Remember my saved draft.\n\nhttps://example.com/saved');
  await expect(page.getByRole('list', { name: 'Attached files' }).getByRole('listitem')).toHaveCount(2);
  const response = page.waitForResponse(value => value.url().endsWith('/messages') && value.request().method() === 'POST');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const result = await response;
  expect(result.status()).toBe(200);
  expect(result.request().postDataJSON()).toMatchObject({ examples: [sample], headlines: ['A fictional headline'], source_urls: ['https://example.com/saved'] });
});

test('request-response groups share one chat scroll and preserve the reader position', async ({ page }, testInfo) => {
  await addSamples(page);
  await send(page, 'Remember these preferences.');
  await send(page, 'Generate two drafts.');
  const history = page.getByRole('region', { name: 'Message history', exact: true });
  await expect(history.locator('.conversation-turn')).toHaveCount(2);
  const sidebarBox = (await page.getByRole('complementary', { name: 'Chat sidebar' }).boundingBox())!;
  const historyBox = (await history.boundingBox())!;
  expect(sidebarBox.x).toBe(0);
  expect(sidebarBox.x + sidebarBox.width).toBeLessThanOrEqual(historyBox.x);
  expect(Math.abs(historyBox.x + historyBox.width - page.viewportSize()!.width)).toBeLessThan(1);
  await expect(page.getByRole('banner')).toHaveCount(0);
  await expect(page.getByRole('complementary', { name: 'Drafts and references' })).toHaveCount(0);
  const firstTurnBox = (await history.locator('.conversation-turn').first().boundingBox())!;
  const composerBox = (await page.getByRole('form', { name: 'Message composer' }).boundingBox())!;
  expect(Math.abs(firstTurnBox.x + firstTurnBox.width / 2 - historyBox.x - historyBox.width / 2)).toBeLessThan(1);
  expect(Math.abs(composerBox.x - firstTurnBox.x)).toBeLessThan(1);
  expect(Math.abs(composerBox.width - firstTurnBox.width)).toBeLessThan(1);
  await expect(history.getByLabel('Conversation date')).toHaveCount(1);
  await expect(history.locator('.message time')).toHaveCount(0);
  expect(await history.locator('.conversation-turn').nth(1).evaluate(element => getComputedStyle(element).borderTopWidth)).toBe('1px');
  for (const turn of await history.locator('.conversation-turn').all()) {
    const turnBox = (await turn.boundingBox())!;
    const userBox = (await turn.getByRole('article', { name: 'You message', exact: true }).boundingBox())!;
    const assistantBox = (await turn.getByRole('article', { name: 'FableAI message', exact: true }).boundingBox())!;
    expect(Math.abs(userBox.x + userBox.width - turnBox.x - turnBox.width)).toBeLessThan(1);
    expect(userBox.x).toBeGreaterThan(assistantBox.x + 20);
    expect(Math.abs(assistantBox.x - turnBox.x)).toBeLessThan(1);
    expect(await turn.evaluate(element => getComputedStyle(element).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
    expect(await turn.locator('.message.user').evaluate(element => getComputedStyle(element).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
    expect(await turn.locator('.message.assistant').evaluate(element => getComputedStyle(element).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
    await expect(turn.getByRole('article', { name: 'You message', exact: true })).toHaveCount(1);
    await expect(turn.getByRole('article', { name: 'FableAI message', exact: true })).toHaveCount(1);
    await expect(turn.getByLabel('Exact response JSON')).toBeVisible();
    await expect(turn.getByRole('button', { name: 'Copy JSON' })).toHaveText('');
  }
  const metrics = await history.evaluate(element => ({
    scrollable: element.scrollHeight > element.clientHeight,
    code: [...element.querySelectorAll('pre')].map(block => ({ height: block.clientHeight, content: block.scrollHeight, overflow: getComputedStyle(block).overflowY })),
  }));
  expect(metrics.scrollable).toBe(true);
  expect(metrics.code.every(block => block.content <= block.height + 1 && block.overflow === 'visible')).toBe(true);
  await history.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
  const json = history.getByLabel('Exact response JSON').first();
  await json.hover();
  const start = await history.evaluate(element => element.scrollTop);
  await page.mouse.wheel(0, 240);
  await expect.poll(() => history.evaluate(element => element.scrollTop)).toBeGreaterThan(start);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await history.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
  await send(page, 'Save a slow preference.');
  await expect(history.locator('.conversation-turn')).toHaveCount(3);
  expect(await history.evaluate(element => element.scrollTop)).toBe(0);
  const heading = history.locator('.json-heading').first();
  const button = heading.getByRole('button', { name: 'Copy JSON' });
  expect((await heading.boundingBox())!.x + (await heading.boundingBox())!.width - ((await button.boundingBox())!.x + (await button.boundingBox())!.width)).toBeLessThan(10);
  await page.screenshot({ path: testInfo.outputPath('grouped-chat-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await history.evaluate(element => getComputedStyle(element).overflowY)).toBe('auto');
  expect(await history.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const mobileHistory = (await history.boundingBox())!;
  expect(Math.abs(mobileHistory.x + mobileHistory.width - page.viewportSize()!.width)).toBeLessThan(1);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
  const mobileTurn = (await history.locator('.conversation-turn').first().boundingBox())!;
  const mobileComposer = (await page.getByRole('form', { name: 'Message composer' }).boundingBox())!;
  expect(Math.abs(mobileTurn.x - mobileComposer.x)).toBeLessThan(1);
  expect(Math.abs(mobileTurn.width - mobileComposer.width)).toBeLessThan(1);
  await page.screenshot({ path: testInfo.outputPath('chat-mobile.png'), fullPage: true });
});

test('one conversation date and safe Markdown survive rerendering and reload', async ({ page }, testInfo) => {
  const date = page.getByLabel('Conversation date');
  await expect(date).toHaveCount(0);
  const id = await selectedChatId(page);
  const detail = await (await page.request.get(base + '/' + id)).json();
  await send(page, 'Remember this fictional preference.');
  const expectedDate = await page.evaluate(createdAt => new Date(createdAt).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }), detail.created_at);
  await expect(date).toHaveCount(1);
  await expect(date).toHaveText(expectedDate);
  const markdown = [
    '## Draft notes', '',
    '- First item', '- Second item', '',
    '[Source](https://example.com/source)', '',
    '```json', '{ "example": "' + 'x'.repeat(600) + '" }', '```', '',
    '[Unsafe](javascript:alert(1))', '',
    '<img src=x onerror="document.body.dataset.untrustedExecuted=1">', '',
    '![Preview](https://example.com/image.png)',
  ].join('\n');
  await page.route('**/messages?*', async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.items = body.items.map((message: { role: string }) => message.role === 'assistant' ? { ...message, content: markdown } : message);
    await route.fulfill({ response, json: body });
  });
  await page.reload();
  const reply = page.getByRole('article', { name: 'FableAI message', exact: true });
  await expect(reply.getByRole('heading', { name: 'Draft notes' })).toBeVisible();
  await expect(reply.getByRole('listitem')).toHaveCount(2);
  await expect(reply.getByRole('link', { name: 'Source' })).toHaveAttribute('href', 'https://example.com/source');
  await expect(reply.getByRole('link', { name: 'Unsafe' })).not.toHaveAttribute('href', /^javascript:/);
  await expect(reply.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => document.body.dataset.untrustedExecuted)).toBeUndefined();
  await expect(date).toHaveCount(1);
  await expect(date).toHaveText(expectedDate);
  expect(await date.evaluate(element => Number(getComputedStyle(element).fontWeight))).toBeGreaterThanOrEqual(600);
  await page.getByLabel('Your message').fill('A draft causes a rerender.');
  await expect(date).toHaveCount(1);
  await expect(page.locator('.message time')).toHaveCount(0);
  for (const width of [1365, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(await reply.locator('.message-content pre').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  }
  await page.screenshot({ path: testInfo.outputPath('markdown-small-screen.png'), fullPage: true });
});

test('saved processing status updates without a refresh control or another submission', async ({ page }) => {
  const id = await selectedChatId(page);
  let reads = 0;
  let submissions = 0;
  await page.route('**/conversations/' + id, async route => {
    const response = await route.fetch();
    const detail = await response.json();
    reads += 1;
    await route.fulfill({ response, json: { ...detail, processing: reads === 1 } });
  });
  await page.route('**/messages', route => {
    if (route.request().method() === 'POST') submissions += 1;
    return route.continue();
  });
  await page.reload();
  await expect(page.getByText(/Its status is checked automatically/)).toBeVisible();
  await expect(page.getByLabel('Your message')).toBeDisabled();
  await expect(page.getByLabel('Your message')).toBeEnabled({ timeout: 7000 });
  expect(reads).toBe(2);
  expect(submissions).toBe(0);
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);
});

test('chat history scrolls and paginates independently from the conversation', async ({ page }) => {
  const id = await selectedChatId(page);
  for (let index = 0; index < 51; index += 1) expect((await page.request.post(base)).status()).toBe(201);
  await page.reload();
  const chats = page.getByRole('navigation', { name: 'Chats', exact: true });
  await expect(chats.getByRole('button', { name: 'Load older chats' })).toBeEnabled();
  await expect(page.locator('.chat-link[aria-current="page"]')).toHaveAttribute('data-conversation-id', id);
  const history = page.getByRole('region', { name: 'Message history', exact: true });
  const historyScroll = await history.evaluate(element => element.scrollTop);
  await chats.hover();
  await page.mouse.wheel(0, 400);
  await expect.poll(() => chats.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  expect(await history.evaluate(element => element.scrollTop)).toBe(historyScroll);
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeInViewport();
  await chats.getByRole('button', { name: 'Load older chats' }).click();
  await expect(chats.getByRole('button', { name: 'Load older chats' })).toHaveCount(0);
  const ids = await chats.locator('.chat-link').evaluateAll(elements => elements.map(element => element.getAttribute('data-conversation-id')));
  expect(new Set(ids).size).toBe(ids.length);
  await expect(page.locator('.chat-link[aria-current="page"]')).toHaveAttribute('data-conversation-id', id);
});
