import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { reviewWriting } from '../src/editorialReview';
import type { ChatInput, ChatResponse, Entry, Message } from '../src/types';

interface EditorialCase {
  id: string;
  instruction: string;
  candidate: Omit<Entry, 'id' | 'version'>;
  expected_findings: string[];
}

const { cases } = JSON.parse(readFileSync(new URL('../../api/tests/fixtures/editorial_cases.json', import.meta.url), 'utf8')) as { cases: EditorialCase[] };

for (const fixture of cases) {
  test('editorial suggestions: ' + fixture.id, () => {
    const before = JSON.stringify(fixture.candidate);
    const findings = reviewWriting(Object.freeze(fixture.candidate), fixture.instruction);
    expect(findings.map(finding => finding.code).sort()).toEqual([...fixture.expected_findings].sort());
    expect(JSON.stringify(fixture.candidate)).toBe(before);
    expect(reviewWriting(fixture.candidate, fixture.instruction)).toEqual(findings);
  });
}

test('literal requirements respect negation, word boundaries, counts and Unicode', () => {
  const candidate = { ...cases[1].candidate, title: '[FICTIONAL] A Search Party Reaches the River', body: 'LOTROOnline is a distinct word. A scout reaches the eastern trail. Café visitors ask for directions.' };
  expect(reviewWriting(candidate, 'Mention "LOTRO" at least once.').map(item => item.code)).toContain('explicit_wording');
  expect(reviewWriting(candidate, 'Do not mention "LOTRO".').map(item => item.code)).not.toContain('explicit_wording');
  expect(reviewWriting(candidate, 'Avoid mentioning "the eastern trail".').map(item => item.code)).toContain('explicit_wording');
  expect(reviewWriting(candidate, 'Mention “CAFE\u0301” at least once.')).toEqual([]);
  expect(reviewWriting(candidate, 'Mention "the eastern trail" exactly 2 times.').map(item => item.code)).toContain('explicit_wording');
  expect(reviewWriting(candidate, 'Mention "the eastern trail" exactly once.')).toEqual([]);
  expect(reviewWriting(candidate, 'Mention "LOTRO" exactly 0 times.')).toEqual([]);
  expect(reviewWriting(candidate, 'Mention "the eastern trail" exactly 0 times.').map(item => item.code)).toContain('explicit_wording');
  expect(reviewWriting(candidate, 'Mention "the eastern trail" AT LEAST TWICE.').map(item => item.code)).toContain('explicit_wording');
});

test('format checks respect negative requirements and skip unknown semantic judgments', () => {
  const candidate = { ...cases[4].candidate, body: '- Our website has a resource library.\n- A contact form collects feedback.' };
  expect(reviewWriting(candidate, 'Use bullet points.')).toEqual([]);
  expect(reviewWriting(candidate, 'Do not use bullet points.').map(item => item.code)).toContain('explicit_format');
  const prose = { ...candidate, body: 'Our website brings guides and contact details together.' };
  expect(reviewWriting(prose, 'Do not use bullet points.')).toEqual([]);
  expect(reviewWriting(prose, 'Make it profound and politically nuanced.')).toEqual([]);
});

test('intentional repetition and non-English prose do not receive English style rules', () => {
  const repeated = { ...cases[1].candidate, body: 'The lantern waits beside the old bridge. The lantern waits beside the old bridge.' };
  expect(reviewWriting(repeated, 'Use intentional repetition as a refrain.')).toEqual([]);
  const foreign = { ...cases[0].candidate, language: 'tr', title: '[FICTIONAL] Haber', body: 'Deep within the digital expanses, a storyteller begins a scene.' };
  expect(reviewWriting(foreign)).toEqual([]);
});

test('writing suggestions remain local while entries are reviewed, revised and restored', async ({ page }, testInfo) => {
  const base = '/api/v1/conversations';
  const id = '11111111-1111-4111-8111-111111111111';
  const entryId = '22222222-2222-4222-8222-222222222222';
  const date = '2026-10-29T12:00:00Z';
  const original = cases.find(item => item.id === 'lotro-padded')!;
  const improved = cases.find(item => item.id === 'lotro-direct')!;
  let entry: Entry = { ...original.candidate, id: entryId, version: 1 };
  const initial: ChatResponse = { kind: 'generation', conversation_id: id, message_id: 'initial-response', assistant_message: 'Created 1 fictional draft for review. Length notice: target 700-1200 characters. Kept the original text.', entries: [structuredClone(entry)] };
  const input: ChatInput = { instruction: original.instruction, examples: [], headlines: [], source_urls: [], revision_targets: [] };
  let messages: Message[] = [
    { message_id: 'initial-request', submission_id: 'first-turn', sequence: 1, role: 'user', content: input.instruction, created_at: date, request: input, response: null },
    { message_id: 'initial-response', submission_id: 'first-turn', sequence: 2, role: 'assistant', content: initial.assistant_message, created_at: date, request: null, response: initial },
  ];
  const submissions: ChatInput[] = [];
  let reviews = 0;
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await page.route('**/api/v1/conversations**', async route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (method === 'PATCH') {
      entry = { ...entry, status: route.request().postDataJSON().status };
      reviews += 1;
      return route.fulfill({ json: entry });
    }
    if (method === 'POST' && url.pathname.endsWith('/messages')) {
      const body = route.request().postDataJSON() as ChatInput;
      submissions.push(body);
      entry = { ...improved.candidate, id: entryId, version: 2 };
      const response: ChatResponse = { kind: 'revision', conversation_id: id, message_id: 'revision-response', assistant_message: 'Revised 1 selected fictional draft. Other entries are unchanged.', entries: [structuredClone(entry)] };
      messages = [...messages,
        { message_id: 'revision-request', submission_id: 'second-turn', sequence: 3, role: 'user', content: body.instruction, created_at: date, request: body, response: null },
        { message_id: 'revision-response', submission_id: 'second-turn', sequence: 4, role: 'assistant', content: response.assistant_message, created_at: date, request: null, response },
      ];
      return route.fulfill({ json: response, headers: { 'Idempotency-Key': route.request().headers()['idempotency-key'] } });
    }
    if (url.pathname.endsWith('/messages')) return route.fulfill({ json: { items: messages, next_after: null } });
    const summary = { conversation_id: id, created_at: date, updated_at: date, revision: entry.version, processing: false };
    if (url.pathname === base) return route.fulfill({ json: { items: [summary], next_offset: null } });
    return route.fulfill({ json: { ...summary, references: { examples: [], headlines: [], urls: [] }, entries: [entry] } });
  });
  await page.goto('/');
  const card = page.getByRole('article', { name: original.candidate.title, exact: true });
  await expect(card).toBeVisible();
  await expect(card.locator('.preserve-text')).toHaveText(original.candidate.body);
  const suggestions = card.locator('.editorial-review');
  await suggestions.locator('summary').click();
  await expect(suggestions).toContainText('Suggestions only');
  await expect(suggestions.getByRole('listitem')).toHaveCount(5);
  expect(JSON.parse(await page.getByLabel('Exact response JSON').innerText())).toEqual(initial);
  for (const width of [1365, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await suggestions.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByLabel('Message composer')).toBeInViewport();
    if (width === 1365 || width === 390) await page.screenshot({ path: testInfo.outputPath('writing-review-' + width + '.png'), fullPage: true });
  }
  expect(submissions).toHaveLength(0);
  await card.getByRole('button', { name: 'Accept', exact: true }).click();
  expect(reviews).toBe(1);
  await page.reload();
  await expect(card.getByRole('button', { name: 'Accept', exact: true })).toBeDisabled();
  expect(JSON.parse(await page.getByLabel('Exact response JSON').innerText())).toEqual(initial);
  await card.getByRole('button', { name: 'Select for revision', exact: true }).click();
  await expect(card.getByRole('button', { name: 'Select for revision', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByLabel('Your message').fill('Make the selected draft clearer.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const revised = page.getByRole('article', { name: improved.candidate.title, exact: true });
  await expect(revised.locator('.preserve-text')).toHaveText(improved.candidate.body);
  await expect(revised.locator('.editorial-review')).toHaveCount(0);
  expect(submissions).toHaveLength(1);
  expect(submissions[0].instruction).toBe('Make the selected draft clearer.');
  expect(submissions[0].revision_targets).toEqual([{ entry_id: entryId, expected_version: 1 }]);
  expect(JSON.stringify(submissions[0])).not.toContain('Writing review');
  await revised.getByRole('button', { name: 'Reject', exact: true }).click();
  await page.reload();
  await expect(revised.getByRole('button', { name: 'Reject', exact: true })).toBeDisabled();
  expect(reviews).toBe(2);
  const latest = JSON.parse(await page.getByLabel('Exact response JSON').last().innerText());
  expect(latest.entries[0]).toEqual({ ...improved.candidate, id: entryId, version: 2 });
});
