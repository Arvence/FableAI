import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react';
import { readSession, writeSession } from './session';
import type { ChatInput } from './types';

interface Draft {
  instruction: string;
  examples: string[];
  headlines: string;
  urls: string;
}

const empty: Draft = { instruction: '', examples: [], headlines: '', urls: '' };
const lines = (value: string) => value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);

export function Composer({ conversationId, disabled, targets, reset, onSend, onClearTargets }: { conversationId: string; disabled: boolean; targets: ChatInput['revision_targets']; reset: number; onSend: (input: ChatInput) => Promise<void>; onClearTargets: () => void }) {
  const [draft, setDraft] = useState(() => readSession(`draft:${conversationId}`, empty));
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const previousReset = useRef(reset);
  const previousTargets = useRef(targets.length);
  const refocus = useRef(false);

  useEffect(() => {
    writeSession(`draft:${conversationId}`, draft);
  }, [conversationId, draft]);

  useEffect(() => {
    if (previousReset.current !== reset) {
      setDraft(empty);
      setError('');
      previousReset.current = reset;
      refocus.current = true;
    }
  }, [reset]);

  useEffect(() => {
    if (!disabled && refocus.current) {
      input.current?.focus();
      refocus.current = false;
    }
  }, [disabled, reset]);

  useEffect(() => {
    if (targets.length > previousTargets.current) input.current?.focus();
    previousTargets.current = targets.length;
  }, [targets.length]);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (disabled || reading) return;
    const body: ChatInput = {
      instruction: draft.instruction.trim(),
      examples: draft.examples.map(text => text.trim()).filter(Boolean),
      headlines: lines(draft.headlines),
      source_urls: lines(draft.urls),
      revision_targets: targets,
    };
    const references = [...body.examples, ...body.headlines, ...body.source_urls];
    if (!body.instruction) return setError('Write an instruction before sending.');
    if (references.length > 10 || references.join('').length > 30000) return setError('Use at most 10 references and 30,000 reference characters per message.');
    if (body.headlines.some(text => text.length > 200)) return setError('Each headline must be 200 characters or fewer.');
    if (body.source_urls.length > 5) return setError('Use at most 5 URL references.');
    if (body.source_urls.some(value => {
      try { return new URL(value).protocol !== 'https:' || value.length > 2048; } catch { return true; }
    })) return setError('Each URL must be a complete HTTPS address of at most 2,048 characters.');
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
      if (draft.examples.length + files.length > 10) throw new Error('Use at most 10 sample texts.');
      const examples = await Promise.all(files.map(async file => {
        if (!file.name.toLowerCase().endsWith('.txt') || file.size > 40000) throw new Error('Attach small plain-text .txt files only (at most 40 KB each).');
        const text = await file.text();
        if (!text.trim() || text.length > 10000 || text.includes('\0')) throw new Error('Each sample must contain 1–10,000 plain-text characters.');
        return text;
      }));
      setDraft(value => ({ ...value, examples: [...value.examples, ...examples] }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The files could not be read.');
    } finally {
      setReading(false);
    }
  }

  return <form className="composer" onSubmit={send} aria-label="Message composer">
    {targets.length > 0 && <div className="target-note">Revising {targets.length} selected {targets.length === 1 ? 'entry' : 'entries'}. <button type="button" onClick={onClearTargets} disabled={disabled}>Clear targets</button></div>}
    <fieldset disabled={disabled || reading}>
      <legend className="sr-only">New message</legend>
      <label htmlFor="instruction">Your instruction</label>
      <textarea id="instruction" ref={input} value={draft.instruction} onChange={event => setDraft({ ...draft, instruction: event.target.value })} onKeyDown={keyDown} maxLength={4000} rows={3} required aria-describedby="send-help" placeholder="Ask for fictional drafts, answer a question, or refine an entry…" />
      <details className="attachments">
        <summary>Add samples, headlines, or URLs</summary>
        <p className="muted">Use authorized, non-confidential examples. URLs are saved as links; pasting one does not fetch it.</p>
        {draft.examples.map((text, index) => <div className="sample-input" key={index}>
          <label htmlFor={`sample-${index}`}>Sample text {index + 1}</label>
          <textarea id={`sample-${index}`} value={text} rows={3} maxLength={10000} onChange={event => setDraft({ ...draft, examples: draft.examples.map((value, i) => i === index ? event.target.value : value) })} />
          <button type="button" aria-label={`Remove sample text ${index + 1}`} onClick={() => setDraft({ ...draft, examples: draft.examples.filter((_, i) => i !== index) })}>Remove</button>
        </div>)}
        <div className="toolbar"><button type="button" disabled={draft.examples.length >= 10} onClick={() => setDraft({ ...draft, examples: [...draft.examples, ''] })}>Add sample text</button></div>
        <label htmlFor="sample-files">Attach sample text files (.txt)</label>
        <input id="sample-files" type="file" accept=".txt,text/plain" multiple onChange={attach} />
        <label htmlFor="headlines">Headlines — one per line</label>
        <textarea id="headlines" value={draft.headlines} rows={3} maxLength={2010} onChange={event => setDraft({ ...draft, headlines: event.target.value })} />
        <label htmlFor="reference-urls">Reference URLs — one HTTPS link per line</label>
        <textarea id="reference-urls" value={draft.urls} rows={2} maxLength={10245} onChange={event => setDraft({ ...draft, urls: event.target.value })} />
      </details>
      {error && <p role="alert" className="error-text">{error}</p>}
      <div className="send-row"><span id="send-help" className="muted">Enter to send · Shift+Enter for a new line</span><button className="primary" type="submit">Send</button></div>
    </fieldset>
    {reading && <p role="status">Reading sample files…</p>}
  </form>;
}
