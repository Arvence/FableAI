import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const repositoryUrl = 'https://github.com/Arvence/FableAI';

async function openWorkspace(page: Page, initialCount = 2) {
  let count = initialCount;
  const created = '2026-10-27T09:00:00Z';
  const id = (index: number) => '00000000-0000-4000-8000-' + String(index + 1).padStart(12, '0');
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await page.route('**/api/v1/conversations**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/messages')) return route.fulfill({ json: { items: [], next_after: null } });
    if (path.endsWith('/conversations')) return route.fulfill({ json: { items: Array.from({ length: count }, (_, index) => ({ conversation_id: id(index), created_at: created, updated_at: created, revision: 0, processing: false })), next_offset: null } });
    return route.fulfill({ json: { conversation_id: path.split('/').pop(), created_at: created, updated_at: created, revision: 0, processing: false, references: { examples: [], headlines: [], urls: [] }, entries: [] } });
  });
  await page.goto('/');
  await expect(page.getByLabel('Your message')).toBeEnabled();
  return async (nextCount: number) => {
    count = nextCount;
    await page.reload();
    await expect(page.getByLabel('Your message')).toBeEnabled();
  };
}

test('sidebar keeps controls and the repository pinned with two or 2000 chats', async ({ page, context }, testInfo) => {
  const changeCount = await openWorkspace(page);
  const github = page.getByRole('link', { name: 'GitHub Repository', exact: true });
  await expect(github).toHaveAttribute('href', repositoryUrl);
  await expect(github).toHaveAttribute('target', '_blank');
  await expect(github).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(github).toHaveAttribute('title', 'GitHub Repository');
  await expect(github).toHaveText('');
  expect(await github.locator('svg').evaluate(element => getComputedStyle(element).fill)).not.toBe('none');
  const initialFooter = (await page.locator('.sidebar-footer').boundingBox())!;
  const initialTop = (await page.locator('.sidebar-top').boundingBox())!;
  const expandedWidth = (await page.locator('.sidebar').boundingBox())!.width;
  await changeCount(2000);
  const chats = page.getByRole('navigation', { name: 'Chats', exact: true });
  await expect(chats.locator('.chat-link')).toHaveCount(2000);
  expect(await chats.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  const history = page.getByRole('region', { name: 'Message history', exact: true });
  const historyScroll = await history.evaluate(element => element.scrollTop);
  await chats.hover();
  await page.mouse.wheel(0, 600);
  await expect.poll(() => chats.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  await chats.evaluate(element => { element.scrollTop = element.scrollHeight; });
  expect(await history.evaluate(element => element.scrollTop)).toBe(historyScroll);
  expect(await page.locator('.sidebar-top').boundingBox()).toEqual(initialTop);
  expect(await page.locator('.sidebar-footer').boundingBox()).toEqual(initialFooter);
  await expect(github).toBeInViewport();
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeInViewport();
  expect(Math.abs(initialFooter.y + initialFooter.height - page.viewportSize()!.height)).toBeLessThan(1);
  await page.screenshot({ path: testInfo.outputPath('soft-dark-sidebar-expanded.png'), fullPage: true });
  await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click();
  expect((await page.locator('.sidebar').boundingBox())!.width).toBeLessThan(expandedWidth);
  await expect(github).toBeInViewport();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByLabel('Your message')).toBeEnabled();
  await github.focus();
  expect(await github.evaluate(element => getComputedStyle(element).outlineStyle)).toBe('solid');
  await page.screenshot({ path: testInfo.outputPath('soft-dark-sidebar-collapsed.png'), fullPage: true });
  await context.route('https://github.com/**', route => route.fulfill({ contentType: 'text/html', body: '<title>Repository link test</title>' }));
  const opened = context.waitForEvent('page');
  await github.click();
  const popup = await opened;
  await popup.waitForLoadState();
  expect(popup.url()).toBe(repositoryUrl);
  expect(await popup.evaluate(() => window.opener === null)).toBe(true);
  await popup.close();
});

for (const width of [320, 390]) {
  test('mobile sidebar drawer keeps navigation accessible at ' + width + 'px', async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 740 });
    await openWorkspace(page, 2000);
    const expand = page.getByRole('button', { name: 'Expand sidebar', exact: true });
    const github = page.getByRole('link', { name: 'GitHub Repository', exact: true });
    await expect(expand).toHaveAttribute('aria-expanded', 'false');
    await expect(github).toBeInViewport();
    await expect(page.getByLabel('Message composer')).toBeInViewport();
    await expand.click();
    const drawer = page.getByRole('dialog', { name: 'Chat sidebar', exact: true });
    await expect(drawer).toHaveAttribute('aria-modal', 'true');
    expect(await page.locator('main').evaluate(element => (element as HTMLElement).inert)).toBe(true);
    await expect(drawer.getByRole('heading', { name: 'FableAI', exact: true })).toBeVisible();
    const footer = (await github.boundingBox())!;
    const chats = drawer.getByRole('navigation', { name: 'Chats', exact: true });
    await chats.evaluate(element => { element.scrollTop = element.scrollHeight; });
    expect(await github.boundingBox()).toEqual(footer);
    await expect(github).toBeInViewport();
    await github.focus();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(github).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath('soft-dark-mobile-drawer.png'), fullPage: true });
    await page.keyboard.press('Escape');
    await expect(expand).toBeFocused();
    expect(await page.locator('main').evaluate(element => (element as HTMLElement).inert)).toBe(false);
    await expand.click();
    await drawer.locator('.chat-link').first().click();
    await expect(expand).toBeFocused();
    await expand.click();
    await page.getByRole('button', { name: 'Close sidebar', exact: true }).click({ position: { x: width - 5, y: 20 } });
    await expect(expand).toBeFocused();
    await expand.click();
    await drawer.getByRole('button', { name: 'Delete chat', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Delete this chat?', exact: true })).toBeVisible();
    await expect(drawer).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'Delete chat', exact: true })).toBeFocused();
    await page.getByRole('button', { name: 'Writing options', exact: true }).click();
    await expect(page.getByRole('combobox', { name: 'Content type', exact: true })).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath('soft-dark-mobile-writing-options-drawer.png'), fullPage: true });
    await page.getByRole('button', { name: 'Close writing options', exact: true }).click();
    await expect(page.getByLabel('Your message')).toBeInViewport();
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth && document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
    const sidebar = (await page.locator('.sidebar').boundingBox())!;
    const composer = (await page.getByLabel('Message composer').boundingBox())!;
    expect(composer.x).toBeGreaterThanOrEqual(sidebar.x + sidebar.width);
    await page.screenshot({ path: testInfo.outputPath('soft-dark-mobile-writing-options.png'), fullPage: true });
  });
}

test('theme text and action colors meet AA contrast across the workspace surfaces', async ({ page }) => {
  await openWorkspace(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const pairs = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    const color = (name: string) => style.getPropertyValue('--' + name).trim();
    const backgrounds = ['bg-app', 'bg-sidebar', 'bg-surface', 'bg-hover', 'bg-card', 'bg-inset'];
    const foregrounds = ['text-primary', 'text-secondary', 'accent-primary', 'accent-hover', 'accent-secondary', 'status-success', 'status-warning', 'status-error'];
    return [...backgrounds.flatMap(background => foregrounds.map(foreground => ({ name: foreground + ' on ' + background, foreground: color(foreground), background: color(background), minimum: 4.5 }))), { name: 'primary button', foreground: color('bg-app'), background: color('accent-primary'), minimum: 4.5 }, { name: 'input boundary', foreground: color('border-control'), background: color('bg-surface'), minimum: 3 }];
  });
  const luminance = (hex: string) => {
    const values = hex.slice(1).match(/../g)!.map(value => {
      const channel = parseInt(value, 16) / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
  };
  for (const pair of pairs) {
    const light = Math.max(luminance(pair.foreground), luminance(pair.background));
    const dark = Math.min(luminance(pair.foreground), luminance(pair.background));
    expect((light + 0.05) / (dark + 0.05), pair.name).toBeGreaterThanOrEqual(pair.minimum);
  }
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
});


test('neutral dark surfaces and accents retain visible keyboard focus', async ({ page }) => {
  await openWorkspace(page);
  const palette = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return { scheme: style.colorScheme, accents: ['accent-primary', 'accent-hover', 'accent-secondary'].map(name => style.getPropertyValue('--' + name).trim()), surfaces: ['bg-app', 'bg-sidebar', 'bg-surface', 'bg-hover', 'bg-card', 'bg-inset'].map(name => style.getPropertyValue('--' + name).trim()) };
  });
  expect(palette.scheme).toBe('dark');
  for (const hex of palette.surfaces) {
    const [red, green, blue] = hex.slice(1).match(/../g)!.map(value => parseInt(value, 16));
    expect(Math.min(red, green, blue)).toBeGreaterThan(15);
    expect(Math.max(red, green, blue)).toBeLessThan(70);
    expect(Math.max(red, green, blue) - Math.min(red, green, blue)).toBe(0);
  }
  for (const hex of palette.accents) {
    const channels = hex.slice(1).match(/../g)!.map(value => parseInt(value, 16));
    expect(Math.max(...channels) - Math.min(...channels)).toBe(0);
  }
  const input = page.getByRole('spinbutton', { name: 'Drafts', exact: true });
  await input.focus();
  expect(await input.evaluate(element => getComputedStyle(element).outlineStyle)).toBe('solid');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await input.evaluate(element => getComputedStyle(element).transitionDuration)).toBe('0s');
});
