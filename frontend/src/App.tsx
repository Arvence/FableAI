import { useEffect, useState } from 'react';
import { ApiError, listConversations, loadConversation, request, review, submit } from './api';
import { Composer } from './Composer';
import { DraftCards } from './DraftCards';
import { JsonView } from './JsonView';
import { MessageHistory } from './MessageHistory';
import { ReferencePanel } from './ReferencePanel';
import { readSession, writeSession } from './session';
import type { ChatInput, Conversation, ConversationSummary, Entry, ImportInput, ImportResponse, Message, PendingSubmission } from './types';

const failure = (error: unknown) => error instanceof ApiError ? error : new ApiError('The action could not be completed. Refresh the chat and try again.');

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
  const [targets, setTargets] = useState<ChatInput['revision_targets']>([]);
  const [reset, setReset] = useState(0);
  const [lastImport, setLastImport] = useState<ImportResponse | null>(null);
  const [reload, setReload] = useState(0);

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
  }, [reload]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    writeSession('selected', selected);
    setConversation(null);
    setMessages([]);
    setTargets([]);
    setLastImport(null);
    setNotice('');
    setError(null);
    setPending(readSession(`pending:${selected}`, null));
    setLoading(true);
    loadConversation(selected, controller.signal).then(state => {
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
  }, [selected, reload]);

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
        setLastImport(result);
        setNotice('Reference imported and saved.');
      } else {
        setReset(value => value + 1);
        writeSession(`draft:${selected}`, null);
        setTargets([]);
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

  async function importReference(body: ImportInput) {
    await perform({ conversationId: selected, key: crypto.randomUUID(), operation: 'references/import', body });
  }

  async function reviewEntry(entry: Entry, status: 'accepted' | 'rejected') {
    setBusy('Saving review…');
    setError(null);
    try {
      await review(selected, entry, status);
      setNotice(`Entry marked ${status}.`);
    } catch (reason) { setError(failure(reason)); }
    finally {
      try { await refresh(); } catch (reason) { setError(failure(reason)); }
      setBusy('');
    }
  }

  function target(entry: Entry) {
    setTargets(value => value.some(item => item.entry_id === entry.id) ? value.filter(item => item.entry_id !== entry.id) : [...value, { entry_id: entry.id, expected_version: entry.version }]);
  }

  const blocked = !!busy || loading;
  const mutationBlocked = blocked || !!pending || !!conversation?.processing;

  return <div className="app-shell">
    <a className="skip-link" href="#instruction">Skip to message input</a>
    <header className="app-header"><div><h1>FableAI</h1><p>Fictional news & announcement drafts</p></div><span className="badge">Local workspace</span></header>
    <nav className="conversation-bar" aria-label="Conversation controls">
      <div className="conversation-select"><label htmlFor="conversation">Conversation</label><select id="conversation" value={selected} onChange={event => setSelected(event.target.value)} disabled={blocked || !conversations.length}>
        {!selected && <option value="">Select a chat</option>}
        {selected && !conversations.some(item => item.conversation_id === selected) && <option value={selected}>Chat {selected.slice(0, 8)}</option>}
        {conversations.map(item => <option key={item.conversation_id} value={item.conversation_id}>Chat {item.conversation_id.slice(0, 8)} · {new Date(item.created_at).toLocaleString()}</option>)}
      </select></div>
      <button type="button" onClick={newChat} disabled={blocked}>New chat</button>
      {nextOffset !== null && <button type="button" onClick={loadMore} disabled={blocked}>Load older chats</button>}
      <button type="button" onClick={() => setReload(value => value + 1)} disabled={blocked}>Refresh</button>
    </nav>
    <div className="feedback">
      <p className="status-line" role="status">{busy || (loading ? 'Loading conversation…' : notice)}</p>
      {error && <div className="error-banner" role="alert"><strong>{error.response?.error.code === 'rate_limited' ? 'Gemini quota reached. Wait before sending again.' : 'Request could not complete.'}</strong><p>{error.message}</p>{error.response && <JsonView value={error.response} />}</div>}
      {pending && !busy && <div className="pending-banner"><p>This submission is not confirmed yet. Retry uses the same request ID and will not duplicate a completed result.</p><button type="button" onClick={() => perform(pending)} disabled={loading}>Retry same submission</button></div>}
      {conversation?.processing && !busy && <p role="status">A saved request is still processing. Use Refresh to check its status. Interrupted requests can take up to three minutes to become recoverable.</p>}
    </div>
    {conversation ? <main className="workspace" aria-busy={!!busy}>
      <div className="chat-column"><h2 className="section-title">Conversation</h2><MessageHistory messages={messages} />
        <Composer key={selected} conversationId={selected} disabled={mutationBlocked} targets={targets} reset={reset} onSend={send} onClearTargets={() => setTargets([])} />
      </div>
      <aside className="context-column" aria-label="Drafts and references">
        <DraftCards entries={conversation.entries} disabled={mutationBlocked} targets={targets} onTarget={target} onReview={reviewEntry} />
        <ReferencePanel references={conversation.references} disabled={mutationBlocked} onImport={importReference} />
        {lastImport && <section className="panel"><h2>Latest import response</h2><JsonView value={lastImport} /></section>}
      </aside>
    </main> : !loading && <main className="welcome"><h2>Your fictional drafting workspace</h2><p>Create a chat to add examples and start a conversation. Saved chats will appear in the selector above.</p><button className="primary" type="button" onClick={newChat} disabled={blocked}>Create your first chat</button></main>}
    <footer>All generated content is synthetic. Nothing is published from this workspace.</footer>
  </div>;
}
