import type { ChatInput, Entry } from './types';

export function DraftCards({ entries, disabled, targets, onTarget, onReview }: { entries: Entry[]; disabled: boolean; targets: ChatInput['revision_targets']; onTarget: (entry: Entry) => void; onReview: (entry: Entry, status: 'accepted' | 'rejected') => Promise<void> }) {
  if (!entries.length) return null;
  return <section className="drafts" aria-label="Generated drafts">
    {entries.map(entry => <article className="draft-card" key={entry.id} aria-label={entry.title}>
      <div className="draft-meta"><span>Fictional / Mock</span><span>{entry.category} / v{entry.version} / {entry.status}</span></div>
      <h3>{entry.title}</h3>
      <p className="preserve-text">{entry.body}</p>
      <div className="draft-actions">
        <button type="button" disabled={disabled || entry.status === 'accepted'} onClick={() => onReview(entry, 'accepted')}>Accept</button>
        <button type="button" disabled={disabled || entry.status === 'rejected'} onClick={() => onReview(entry, 'rejected')}>Reject</button>
        <button type="button" disabled={disabled || (targets.length >= 20 && !targets.some(target => target.entry_id === entry.id))} aria-pressed={targets.some(target => target.entry_id === entry.id)} onClick={() => onTarget(entry)}>Select for revision</button>
      </div>
    </article>)}
  </section>;
}
