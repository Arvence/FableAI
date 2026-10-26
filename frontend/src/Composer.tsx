import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react';
import { readSession, writeSession } from './session';
import { defaultSettings, GenerationOptions, restoreSettings } from './GenerationOptions';
import type { ChatInput } from './types';

interface Attachment {
  id: string;
  name: string;
  text: string;
  kind: 'sample' | 'headline';
}

interface Draft {
  instruction: string;
  attachments: Attachment[];
}

interface PreviousDraft {
  instruction: string;
  examples: string[];
  headlines: string;
  urls: string;
}

const empty: Draft = { instruction: '', attachments: [] };

function restoreDraft(conversationId: string): Draft {
  const saved = readSession<Draft | PreviousDraft>('draft:' + conversationId, empty);
  if ('attachments' in saved) return saved;
  const samples = saved.examples.map((text, index): Attachment => ({ id: crypto.randomUUID(), name: 'Sample ' + (index + 1) + '.txt', text, kind: 'sample' }));
  const headlines = saved.headlines.split(/\r?\n/).filter(text => text.trim()).map((text, index): Attachment => ({ id: crypto.randomUUID(), name: 'Headline ' + (index + 1) + '.txt', text, kind: 'headline' }));
  return { instruction: [saved.instruction, saved.urls].filter(Boolean).join('\n\n'), attachments: [...samples, ...headlines] };
}

function referenceLinks(text: string): string[] {
  const matches = text.match(/https?:\/\/[^\s<>"'\x60]+/gi) ?? [];
  const links = matches.map(match => {
    let url = match.replace(/[.,;:!?]+$/, '');
    for (const [open, close] of [['(', ')'], ['[', ']'], ['{', '}']]) {
      while (url.endsWith(close) && url.split(close).length > url.split(open).length) url = url.slice(0, -1);
    }
    return url;
  });
  return [...new Set(links)];
}

export function Composer({ conversationId, disabled, reset, targets, onClearTargets, onSend }: { conversationId: string; disabled: boolean; reset: number; targets: ChatInput['revision_targets']; onClearTargets: () => void; onSend: (input: ChatInput) => Promise<void> }) {
  const [draft, setDraft] = useState(() => restoreDraft(conversationId));
  const [settings, setSettings] = useState(() => restoreSettings(conversationId));
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const previousReset = useRef(reset);
  const refocus = useRef(false);

  useEffect(() => {
    writeSession('draft:' + conversationId, draft);
  }, [conversationId, draft]);

  useEffect(() => {
    writeSession('settings:' + conversationId, settings);
  }, [conversationId, settings]);

  useEffect(() => {
    if (previousReset.current !== reset) {
      setDraft(empty);
      setError('');
      previousReset.current = reset;
      refocus.current = true;
    }
  }, [reset]);

  useEffect(() => {
    if (!disabled && !reading && refocus.current) {
      input.current?.focus({ preventScroll: true });
      refocus.current = false;
    }
  }, [disabled, reading, reset]);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (disabled || reading) return;
    const body: ChatInput = {
      instruction: draft.instruction.trim(),
      examples: draft.attachments.filter(file => file.kind === 'sample').map(file => file.text.trim()).filter(Boolean),
      headlines: draft.attachments.filter(file => file.kind === 'headline').map(file => file.text.trim()).filter(Boolean),
      source_urls: referenceLinks(draft.instruction),
      revision_targets: targets,
      generation_settings: settings,
    };
    if (!body.instruction) return setError('Write a message before sending.');
    if (body.instruction.length > 4000) return setError('Keep the message within 4,000 characters. Attach longer samples as .txt files.');
    const references = [...body.examples, ...body.headlines, ...body.source_urls];
    if (references.length > 10 || references.join('').length > 30000) return setError('Use at most 10 references and 30,000 reference characters per message.');
    if (body.source_urls.length > 5) return setError('Use at most 5 URL references.');
    if (body.source_urls.some(value => {
      try {
        const url = new URL(value);
        return url.protocol !== 'https:' || !url.hostname || !!url.username || !!url.password || value.length > 2048;
      } catch { return true; }
    })) return setError('Each reference URL must be a complete HTTPS address of at most 2,048 characters, without credentials.');
    setError('');
    refocus.current = true;
    await onSend(body);
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function attach(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    if (!files.length) return;
    setError('');
    setReading(true);
    try {
      if (draft.attachments.length + files.length > 10) throw new Error('Use at most 10 attached text files.');
      const attachments = await Promise.all(files.map(async (file): Promise<Attachment> => {
        if (!file.name.toLowerCase().endsWith('.txt') || file.size > 40000) throw new Error('Attach plain-text .txt files only (at most 40 KB each).');
        const text = await file.text();
        if (!text.trim() || text.length > 10000 || text.includes('\0')) throw new Error('Each file must contain 1–10,000 plain-text characters.');
        return { id: crypto.randomUUID(), name: file.name, text, kind: 'sample' };
      }));
      if ([...draft.attachments, ...attachments].reduce((total, file) => total + file.text.trim().length, 0) > 30000) throw new Error('Attached samples cannot exceed 30,000 characters in total.');
      setDraft(value => ({ ...value, attachments: [...value.attachments, ...attachments] }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The files could not be read.');
    } finally {
      refocus.current = true;
      setReading(false);
    }
  }

  return <form className="composer" onSubmit={send} aria-label="Message composer">
    <fieldset disabled={disabled || reading}>
      <legend className="sr-only">New message</legend>
      <label className="sr-only" htmlFor="instruction">Your message</label>
      {draft.attachments.length > 0 && <ul className="attachment-list" aria-label="Attached files">
        {draft.attachments.map(file => <li className="attachment-tile" key={file.id}>
          <button className="attachment-remove" type="button" aria-label={'Remove ' + file.name} title={'Remove ' + file.name} onClick={() => setDraft(value => ({ ...value, attachments: value.attachments.filter(item => item.id !== file.id) }))}>
            <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 4 8 8M12 4l-8 8" /></svg>
          </button>
          <span className="attachment-name" title={file.name}>{file.name.slice(0, -4)}</span>
          <span className="attachment-extension">{file.name.slice(-4).toUpperCase()}</span>
        </li>)}
      </ul>}
      {targets.length > 0 && <div className="revision-selection" role="status">
        <span>{targets.length} {targets.length === 1 ? 'draft' : 'drafts'} selected for revision</span>
        <button type="button" onClick={onClearTargets}>Clear selection</button>
      </div>}
      <div className="composer-row">
        <button className="icon-button attach-button" type="button" aria-label="Attach text files" title="Attach text files (.txt)" onClick={() => fileInput.current?.click()}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 12 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9M7 13l7-7" /></svg>
        </button>
        <textarea id="instruction" ref={input} value={draft.instruction} onChange={event => setDraft({ ...draft, instruction: event.target.value })} onKeyDown={keyDown} maxLength={4000} rows={1} required placeholder="Message FableAI" />
        <button className="icon-button options-button" type="button" aria-label="Writing options" title="Writing options" aria-expanded={optionsOpen} aria-controls="generation-options" onClick={() => setOptionsOpen(value => !value)}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h6m4 0h6M4 17h10m4 0h2M10 4v6m4 4v6" /></svg>
          {Object.entries(settings).some(([key, value]) => value !== defaultSettings[key as keyof typeof defaultSettings]) && <span className="options-indicator" />}
        </button>
        <button className="icon-button send-button" type="submit" aria-label="Send" title="Send">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 19V5m-6 6 6-6 6 6" /></svg>
        </button>
      </div>
      {optionsOpen && <GenerationOptions settings={settings} onChange={setSettings} />}
      <input ref={fileInput} type="file" accept=".txt,text/plain" multiple hidden aria-label="Attach text files" onChange={attach} />
      {error && <p role="alert" className="error-text">{error}</p>}
    </fieldset>
    {reading && <p className="sr-only" role="status">Reading text files…</p>}
  </form>;
}
