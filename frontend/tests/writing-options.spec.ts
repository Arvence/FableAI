import { expect, test } from '@playwright/test';
import { ApiError, submit } from '../src/api';
import { defaultSettings } from '../src/writingSettings';
import type { ChatInput, PendingSubmission } from '../src/types';

test('request boundary rejects invalid counts before serialization or fetch', async () => {
  const previousFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => { requests += 1; throw new Error('Invalid settings must never reach fetch.'); };
  try {
    for (const count of [0, -1, 21, 2.5, NaN, Infinity, '', 'abc', '3drafts', '1e1', null, undefined]) {
      const body: ChatInput = { instruction: 'rainy afternoons', examples: [], headlines: [], source_urls: [], revision_targets: [], generation_settings: { ...defaultSettings, count: count as number } };
      const pending: PendingSubmission = { key: 'offline-validation', conversationId: 'offline-chat', operation: 'messages', body };
      await expect(submit(pending), 'count: ' + String(count)).rejects.toBeInstanceOf(ApiError);
    }
    await expect(submit({ key: 'budget', conversationId: 'offline-chat', operation: 'messages', body: { instruction: 'rainy afternoons', examples: [], headlines: [], source_urls: [], revision_targets: [], generation_settings: { ...defaultSettings, count: 20, max_body_chars: 1200 } } })).rejects.toThrow('12,000');
    expect(requests).toBe(0);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test.describe('writing options workspace', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
    await expect(page.getByLabel('Your message')).toBeEnabled();
  });

  test('panel stays visible through chat scrolling and can scroll independently', async ({ page }, testInfo) => {
    const panel = page.getByRole('complementary', { name: 'Writing Options', exact: true });
    await expect(panel).toBeVisible();
    await expect(page.locator('#generation-options')).toHaveCount(1);
    await expect(page.getByLabel('Message composer').locator('#generation-options')).toHaveCount(0);
    const panelBox = (await panel.boundingBox())!;
    expect(panelBox.width).toBeGreaterThanOrEqual(260);
    expect(panelBox.width).toBeLessThanOrEqual(320);
    await page.getByLabel('Your message').fill('rainy afternoons');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(page.getByLabel('Exact response JSON')).toBeVisible();
    const history = page.getByRole('region', { name: 'Message history', exact: true });
    await page.setViewportSize({ width: 1365, height: 440 });
    await history.evaluate(element => { element.scrollTop = 0; });
    const composerBox = await page.getByLabel('Message composer').boundingBox();
    const before = await panel.boundingBox();
    await history.hover();
    await page.mouse.wheel(0, 500);
    await expect.poll(() => history.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await panel.boundingBox()).toEqual(before);
    expect(await panel.evaluate(element => element.scrollTop)).toBe(0);
    expect(await page.getByLabel('Message composer').boundingBox()).toEqual(composerBox);
    const historyScroll = await history.evaluate(element => element.scrollTop);
    expect(await panel.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    await panel.hover();
    await page.mouse.wheel(0, 400);
    await expect.poll(() => panel.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    expect(await history.evaluate(element => element.scrollTop)).toBe(historyScroll);
    await expect(page.getByLabel('Message composer')).toBeInViewport();
    await page.setViewportSize({ width: 1365, height: 950 });
    await page.screenshot({ path: testInfo.outputPath('persistent-writing-panel.png'), fullPage: true });
  });

  test('valid typed counts use all currently selected settings on every message', async ({ page }) => {
    const payloads: ChatInput[] = [];
    await page.route('**/api/v1/conversations/*/messages', async route => {
      const body = route.request().postDataJSON() as ChatInput;
      payloads.push(body);
      const id = new URL(route.request().url()).pathname.split('/')[4];
      await route.fulfill({ headers: { 'Idempotency-Key': route.request().headers()['idempotency-key'] }, json: { kind: 'message', conversation_id: id, message_id: crypto.randomUUID(), assistant_message: 'Saved test message.', entries: [] } });
    });
    const count = page.getByRole('spinbutton', { name: 'Drafts', exact: true });
    await expect(count).toHaveValue('1');
    await expect(page.getByLabel('Content type', { exact: true })).toHaveValue('news');
    await expect(page.getByLabel('Tone', { exact: true })).toHaveValue('neutral');
    await expect(page.getByLabel('Language', { exact: true })).toHaveValue('en');
    await expect(page.getByLabel('Body length', { exact: true })).toHaveValue('300');
    await expect(page.getByLabel('Content type', { exact: true }).locator('option')).toHaveText(['News', 'Announcement', 'Update', 'Article', 'Blog']);
    await expect(page.getByLabel('Tone', { exact: true }).locator('option')).toHaveText(['Neutral', 'Formal', 'Informative', 'Playful']);
    await expect(page.getByLabel('Language', { exact: true }).locator('option')).toHaveText(['English', 'Turkish', 'German', 'French', 'Spanish']);
    await page.getByLabel('Content type', { exact: true }).selectOption('blog');
    await page.getByLabel('Tone', { exact: true }).selectOption('informative');
    await page.getByLabel('Language', { exact: true }).selectOption('de');
    for (const value of ['1', '3', '12', '20']) {
      await count.fill(value);
      await expect(count).toHaveAttribute('aria-invalid', 'false');
      await page.getByLabel('Your message').fill('A new community website');
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await expect(page.getByLabel('Your message')).toHaveValue('');
      expect(payloads.at(-1)?.generation_settings).toEqual({ ...defaultSettings, count: Number(value), category: 'blog', tone: 'informative', language: 'de' });
      await expect(count).toHaveValue(value);
    }
    expect(payloads).toHaveLength(4);
    await page.reload();
    await expect(count).toHaveValue('20');
    await expect(page.getByLabel('Content type', { exact: true })).toHaveValue('blog');
    await page.getByRole('button', { name: 'Reset', exact: true }).click();
    await expect(count).toHaveValue('1');
    await expect(page.getByLabel('Content type', { exact: true })).toHaveValue('news');
    await expect(page.getByLabel('Tone', { exact: true })).toHaveValue('neutral');
    await expect(page.getByLabel('Language', { exact: true })).toHaveValue('en');
    await expect(page.getByLabel('Body length', { exact: true })).toHaveValue('300');
  });

  test('invalid edits stay visible and never send a message', async ({ page }) => {
    let requests = 0;
    page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/messages')) requests += 1; });
    const count = page.getByRole('spinbutton', { name: 'Drafts', exact: true });
    await page.getByLabel('Your message').fill('My unsent topic');
    for (const value of ['0', '21', '-1', '2.5', '']) {
      await count.fill(value);
      await expect(count).toHaveValue(value);
      await expect(count).toHaveAttribute('aria-invalid', 'true');
      await expect(page.locator('#draft-count-error')).toHaveText('Enter a whole number from 1 to 20.');
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await expect(count).toBeFocused();
      await expect(count).toHaveValue(value);
      await expect(page.getByLabel('Your message')).toHaveValue('My unsent topic');
      expect(requests).toBe(0);
    }
    await count.pressSequentially('abc');
    await expect(count).toHaveValue('');
    await page.getByLabel('Your message').press('Enter');
    await expect(count).toBeFocused();
    expect(requests).toBe(0);
    await count.fill('12');
    await page.getByLabel('Body length', { exact: true }).selectOption('700');
    await expect(count).toHaveAttribute('aria-invalid', 'false');
    await count.fill('20');
    await expect(page.locator('#draft-count-error')).toContainText('12,000');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    expect(requests).toBe(0);
    await page.getByRole('button', { name: 'Reset', exact: true }).click();
    await expect(page.locator('#draft-count-error')).toHaveCount(0);
    await expect(count).toHaveValue('1');
  });

  test('unfinished edits persist per chat and survive reload without a fallback count', async ({ page }) => {
    const firstId = await page.locator('.chat-link[aria-current="page"]').getAttribute('data-conversation-id');
    const count = page.getByRole('spinbutton', { name: 'Drafts', exact: true });
    await count.fill('');
    await page.getByLabel('Tone', { exact: true }).selectOption('playful');
    await page.getByRole('button', { name: 'New chat', exact: true }).click();
    await expect(count).toHaveValue('1');
    await page.locator('.chat-link[data-conversation-id="' + firstId + '"]').click();
    await expect(count).toHaveValue('');
    await expect(page.getByLabel('Tone', { exact: true })).toHaveValue('playful');
    await page.reload();
    await expect(count).toHaveValue('');
    await expect(count).toHaveAttribute('aria-invalid', 'true');
    await page.getByRole('button', { name: 'Reset', exact: true }).click();
    await expect(count).toHaveValue('1');
  });

  test('one settings form survives responsive changes with keyboard access', async ({ page }, testInfo) => {
    await page.getByRole('spinbutton', { name: 'Drafts', exact: true }).fill('3');
    await page.getByLabel('Body length', { exact: true }).selectOption('700');
    for (const width of [1024, 768, 320, 390, 1365]) {
      await page.setViewportSize({ width, height: 740 });
      if (width <= 1000) {
        const open = page.getByRole('button', { name: 'Writing options', exact: true });
        await expect(open).toHaveAttribute('aria-expanded', 'false');
        await expect(page.getByLabel('Your message')).toBeInViewport();
        await open.click();
        const drawer = page.getByRole('dialog', { name: 'Writing Options', exact: true });
        await expect(drawer).toBeVisible();
        await expect(page.getByLabel('Content type', { exact: true })).toBeFocused();
        await expect(page.getByRole('spinbutton', { name: 'Drafts', exact: true })).toHaveValue('3');
        await expect(page.getByLabel('Body length', { exact: true })).toHaveValue('700');
        await page.getByRole('spinbutton', { name: 'Drafts', exact: true }).focus();
        await page.keyboard.press('Tab');
        await expect(page.getByRole('button', { name: 'Reset', exact: true })).toBeFocused();
        await page.keyboard.press('Shift+Tab');
        await expect(page.getByRole('spinbutton', { name: 'Drafts', exact: true })).toBeFocused();
        if (width === 390) await page.screenshot({ path: testInfo.outputPath('writing-options-drawer.png'), fullPage: true });
        await page.keyboard.press('Escape');
        await expect(open).toBeFocused();
        await expect(page.getByLabel('Your message')).toBeInViewport();
      } else {
        await expect(page.getByRole('complementary', { name: 'Writing Options', exact: true })).toBeVisible();
        await expect(page.getByRole('spinbutton', { name: 'Drafts', exact: true })).toHaveValue('3');
      }
      await expect(page.locator('#generation-options')).toHaveCount(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight)).toBe(true);
    }
    await page.setViewportSize({ width: 390, height: 740 });
    await page.getByRole('button', { name: 'Writing options', exact: true }).click();
    await page.getByRole('spinbutton', { name: 'Drafts', exact: true }).fill('');
    await page.getByRole('button', { name: 'Close writing options', exact: true }).click();
    await page.getByLabel('Your message').fill('Keep this message');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Writing Options', exact: true })).toBeVisible();
    await expect(page.getByRole('spinbutton', { name: 'Drafts', exact: true })).toBeFocused();
    await expect(page.locator('#draft-count-error')).toBeVisible();
  });
});
