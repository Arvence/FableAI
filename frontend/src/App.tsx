import { useEffect, useRef, useState } from 'react';
import { ApiError, listConversations, loadConversation, request, review, submit } from './api';
import { Composer } from './Composer';
import { JsonView } from './JsonView';
import { MessageHistory } from './MessageHistory';
import { readSession, writeSession } from './session';
import type { ChatInput, Conversation, ConversationSummary, Entry, Message, PendingSubmission } from './types';

const failure = (error: unknown) => error instanceof ApiError ? error : new ApiError('The action could not be completed. Reload the page and try again.');

export function App() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [selected, setSelected] = useState<string>(() => readSession('selected', ''));
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState<PendingSubmission | null>(null);
  const [reset, setReset] = useState(0);
  const [targets, setTargets] = useState<ChatInput['revision_targets']>([]);
  const deleteDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    listConversations(0, controller.signal).then(page => {
      setConversations(page.items);
      setNextOffset(page.next_offset);
      setSelected(current => current || page.items[0]?.conversation_id || '');
      setLoading(false);
    }).catch(reason => {
      if (!controller.signal.aborted) { setError(failure(reason)); setLoading(false); }
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    writeSession('selected', selected);
    setConversation(null);
    setMessages([]);
    setTargets([]);
    setNotice('');
    setError(null);
    setPending(readSession(`pending:${selected}`, null));
    setLoading(true);
    loadConversation(selected, controller.signal).then(state => {
      if (controller.signal.aborted) return;
      setConversation(state.detail);
      setMessages(state.messages);
      setLoading(false);
    }).catch(reason => {
      if (controller.signal.aborted) return;
      setError(failure(reason));
      setLoading(false);
      if (reason instanceof ApiError && reason.response?.error.code === 'conversation_not_found') {
        writeSession('selected', null);
        setSelected('');
      }
    });
    return () => controller.abort();
  }, [selected]);

  useEffect(() => {
    if (!selected || !conversation?.processing || busy) return;
    const controller = new AbortController();
    let fetching = false;
    const timer = window.setInterval(async () => {
      if (fetching) return;
      fetching = true;
      try {
        const state = await loadConversation(selected, controller.signal);
        if (!controller.signal.aborted) {
          setConversation(state.detail);
          setMessages(state.messages);
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(failure(reason));
      } finally {
        fetching = false;
      }
    }, 3000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [selected, conversation?.processing, busy]);

  async function refresh() {
    const [page, state] = await Promise.all([listConversations(), loadConversation(selected)]);
    setConversations(page.items);
    setNextOffset(page.next_offset);
    setConversation(state.detail);
    setMessages(state.messages);
  }

  async function newChat() {
    setBusy('Creating a chat…');
    setError(null);
    try {
      const created = await request<Conversation>('', { method: 'POST' });
      setConversations(value => [created, ...value]);
      setSelected(created.conversation_id);
    } catch (reason) {
      setError(failure(reason));
    } finally {
      setBusy('');
    }
  }

  async function deleteChat() {
    if (!selected || busy || loading) return;
    const id = selected;
    deleteDialog.current?.close();
    setBusy('Deleting chat…');
    setError(null);
    setNotice('');
    let deleted = false;
    try {
      try {
        await request<void>(`/${id}`, { method: 'DELETE' });
      } catch (reason) {
        if (!(reason instanceof ApiError) || reason.response?.error.code !== 'conversation_not_found') throw reason;
      }
      deleted = true;
      writeSession(`draft:${id}`, null);
      writeSession(`settings:${id}`, null);
      writeSession(`pending:${id}`, null);
      writeSession('selected', null);
      setSelected('');
      setConversation(null);
      setMessages([]);
      setPending(null);
      setConversations(value => value.filter(item => item.conversation_id !== id));
      setNextOffset(null);
      setNotice('Chat deleted.');
      const page = await listConversations();
      setConversations(page.items);
      setNextOffset(page.next_offset);
      setSelected(page.items[0]?.conversation_id || '');
    } catch (reason) {
      setError(failure(reason));
    } finally {
      setBusy('');
      requestAnimationFrame(() => {
        const activeChat = document.querySelector<HTMLButtonElement>('.chat-link[aria-current="page"]');
        const control = deleted ? (activeChat ?? document.getElementById('new-chat')) : document.getElementById('delete-chat');
        control?.focus();
      });
    }
  }

  async function loadMore() {
    if (nextOffset === null) return;
    setBusy('Loading chats…');
    try {
      const page = await listConversations(nextOffset);
      setConversations(value => [...value, ...page.items.filter(item => !value.some(existing => existing.conversation_id === item.conversation_id))]);
      setNextOffset(page.next_offset);
    } catch (reason) { setError(failure(reason)); } finally { setBusy(''); }
  }

  async function perform(submission: PendingSubmission) {
    if (busy) return;
    setBusy(submission.operation === 'messages' ? 'FableAI is preparing a response…' : 'Importing the authorized reference…');
    setError(null);
    setNotice('');
    setPending(submission);
    if (!writeSession(`pending:${selected}`, submission)) setNotice('Browser session storage is unavailable. Keep this tab open until the request finishes.');
    try {
      const result = await submit(submission);
      setPending(null);
      writeSession(`pending:${selected}`, null);
      if (result.kind === 'reference_import') {
        setNotice('Reference imported and saved.');
      } else {
        if (result.kind === 'generation' || result.kind === 'revision') setTargets([]);
        setReset(value => value + 1);
        writeSession(`draft:${selected}`, null);
        setNotice('Response saved.');
      }
    } catch (reason) {
      const problem = failure(reason);
      setError(problem);
      const code = problem.response?.error.code;
      const uncertain = (code === 'storage_failed' || code === 'internal_error') && !problem.response?.message_id;
      if (problem.response && code !== 'request_in_progress' && !uncertain) {
        setPending(null);
        writeSession(`pending:${selected}`, null);
      }
    } finally {
      try { await refresh(); } catch (reason) { setError(failure(reason)); }
      setBusy('');
    }
  }

  async function send(body: ChatInput) {
    await perform({ conversationId: selected, key: crypto.randomUUID(), operation: 'messages', body });
  }

  function selectDraft(entry: Entry) {
    setTargets(current => current.some(target => target.entry_id === entry.id)
      ? current.filter(target => target.entry_id !== entry.id)
      : [...current, { entry_id: entry.id, expected_version: entry.version }]);
    document.getElementById('instruction')?.focus({ preventScroll: true });
  }

  async function reviewDraft(entry: Entry, status: 'accepted' | 'rejected') {
    setBusy('Saving review...');
    setError(null);
    try {
      await review(selected, entry, status);
      await refresh();
      setNotice('Review saved.');
    } catch (reason) {
      setError(failure(reason));
    } finally {
      setBusy('');
    }
  }

  const blocked = !!busy || loading;
  const mutationBlocked = blocked || !!pending || !!conversation?.processing;

  const chatItems = selected && !conversations.some(item => item.conversation_id === selected)
    ? [{ conversation_id: selected, created_at: conversation?.created_at ?? '' }, ...conversations]
    : conversations;

  return <div className="app-shell">
    <a className="skip-link" href="#instruction">Skip to message input</a>
    <aside className="sidebar" aria-label="Chat sidebar">
      <div className="sidebar-heading"><h1>FableAI</h1></div>
      <nav className="sidebar-actions" aria-label="Conversation controls">
        <button id="new-chat" className="sidebar-action new-chat" type="button" onClick={newChat} disabled={blocked} aria-label="New chat" title="New chat">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14" /></svg><span className="sidebar-label">New chat</span>
        </button>
        <button id="delete-chat" className="sidebar-action danger" type="button" onClick={() => deleteDialog.current?.showModal()} disabled={blocked || !selected} aria-label="Delete chat" title="Delete chat">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg><span className="sidebar-label">Delete chat</span>
        </button>
      </nav>
      <nav className="chat-list" aria-label="Chats">
        <ul>
          {chatItems.map(item => <li key={item.conversation_id}>
            <button className="chat-link" type="button" data-conversation-id={item.conversation_id} aria-current={selected === item.conversation_id ? 'page' : undefined} aria-label={'Chat ' + item.conversation_id.slice(0, 8)} title={'Chat ' + item.conversation_id.slice(0, 8) + (item.created_at ? ' · ' + new Date(item.created_at).toLocaleString() : '')} onClick={() => setSelected(item.conversation_id)} disabled={blocked}>
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M21 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 0 1 19 0Z" /><path d="M7 9h10M7 13h6" /></svg>
              <span className="sidebar-label chat-label"><span>Chat {item.conversation_id.slice(0, 8)}</span>{item.created_at && <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</time>}</span>
            </button>
          </li>)}
        </ul>
        {nextOffset !== null && <button className="sidebar-action older-chats" type="button" onClick={loadMore} disabled={blocked} aria-label="Load older chats" title="Load older chats">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 12h.01M12 12h.01M19 12h.01" strokeWidth="3" /></svg><span className="sidebar-label">Load older chats</span>
        </button>}
      </nav>
    </aside>
    <dialog className="delete-dialog" ref={deleteDialog} aria-labelledby="delete-chat-title" aria-describedby="delete-chat-description">
      <h2 id="delete-chat-title">Delete this chat?</h2>
      <p id="delete-chat-description">This permanently deletes the selected chat, including its messages, references, drafts, and review history.</p>
      <div className="dialog-actions">
        <button type="button" onClick={() => deleteDialog.current?.close()} autoFocus>Cancel</button>
        <button className="danger" type="button" onClick={deleteChat}>Delete chat</button>
      </div>
    </dialog>
    <main className="chat-workspace" aria-busy={blocked}>
      <div className="feedback">
        <p className="sr-only" role="status">{busy || (loading ? 'Loading conversation…' : notice)}</p>
        {error && <div className="error-banner" role="alert"><strong>{error.response?.error.code === 'rate_limited' ? 'Gemini quota reached. Wait before sending again.' : 'Request could not complete.'}</strong><p>{error.message}</p>{error.response && <JsonView value={error.response} />}</div>}
        {pending && !busy && <div className="pending-banner"><p>This submission is not confirmed yet. Retry uses the same request ID and will not duplicate a completed result.</p><button type="button" onClick={() => perform(pending)} disabled={loading}>Retry same submission</button></div>}
        {conversation?.processing && !busy && <p role="status">A saved request is still processing. Its status is checked automatically. Interrupted requests can take up to three minutes to become recoverable.</p>}
      </div>
      {selected ? <>
        <MessageHistory key={selected} messages={messages} createdAt={conversation?.created_at} loading={loading} entries={conversation?.entries ?? []} disabled={mutationBlocked} targets={targets} onTarget={selectDraft} onReview={reviewDraft} />
        <div className="composer-dock"><Composer key={selected} conversationId={selected} disabled={mutationBlocked} reset={reset} targets={targets} onClearTargets={() => setTargets([])} onSend={send} /></div>
      </> : !loading && <section className="welcome"><h2>Start a conversation</h2><button className="primary" type="button" onClick={newChat} disabled={blocked}>Create your first chat</button></section>}
    </main>
  </div>;
}
