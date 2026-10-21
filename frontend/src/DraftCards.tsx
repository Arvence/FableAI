import type { ChatInput, Entry } from './types';

export function DraftCards({ entries, disabled, targets, onTarget, onReview }: { entries: Entry[]; disabled: boolean; targets: ChatInput['revision_targets']; onTarget: (entry: Entry) => void; onReview: (entry: Entry, status: 'accepted' | 'rejected') => Promise<void> }) {
  return <section className="panel drafts" aria-labelledby="drafts-title">
    <h2 id="drafts-title">Current drafts <span className="count">{entries.length}</span></h2>
    <p className="muted">Fictional content for review. Accepting a draft saves your decision locally.</p>
    {!entries.length && <p>No drafts yet. Ask for fictional news or announcements in the chat.</p>}
    {entries.map((entry, index) => <article className="draft-card" key={entry.id} aria-label={`Entry ${index + 1}`}>
      <div className="draft-meta"><span className="badge synthetic">Synthetic · Fictional</span><span>Entry {index + 1} · v{entry.version}</span></div>
      <h3>{entry.title}</h3>
      <p className="preserve-text">{entry.body}</p>
      <p className="muted">{entry.category} · {entry.language}{entry.tags.length ? ` · ${entry.tags.join(', ')}` : ''}</p>
      <div className="toolbar"><span className={`badge ${entry.status}`}>{entry.status}</span>
        <button type="button" disabled={disabled || entry.status === 'accepted'} onClick={() => onReview(entry, 'accepted')}>Accept</button>
        <button type="button" disabled={disabled || entry.status === 'rejected'} onClick={() => onReview(entry, 'rejected')}>Reject</button>
        <button type="button" disabled={disabled || (targets.length >= 5 && !targets.some(target => target.entry_id === entry.id))} aria-pressed={targets.some(target => target.entry_id === entry.id)} onClick={() => onTarget(entry)}>Revise entry {index + 1}</button>
      </div>
    </article>)}
  </section>;
}
