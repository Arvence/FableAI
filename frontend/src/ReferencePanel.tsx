import { useState } from 'react';
import type { FormEvent } from 'react';
import type { ImportInput, References, SourcePermission, URLReference } from './types';

function ReferenceImport({ reference, disabled, onImport }: { reference: URLReference; disabled: boolean; onImport: (input: ImportInput) => Promise<void> }) {
  const [basis, setBasis] = useState<SourcePermission['basis']>('owned');
  const [evidence, setEvidence] = useState('');
  const [access, setAccess] = useState(false);
  const [modelUse, setModelUse] = useState(false);
  const [nonConfidential, setNonConfidential] = useState(false);
  const labels = { not_requested: 'Link only', pending: 'Import pending', imported: 'Imported', failed: 'Blocked / unavailable' };

  async function importReference(event: FormEvent) {
    event.preventDefault();
    if (!access || !evidence.trim() || disabled) return;
    await onImport({ reference_id: reference.id, confirmed: true, permissions: [{ url: reference.url, basis, evidence: evidence.trim(), automated_access_allowed: access, model_use_allowed: modelUse, non_confidential: nonConfidential }] });
  }

  return <article className="reference-url">
    <p className="url-text">{reference.url}</p>
    <span className={`badge ${reference.import_status === 'failed' ? 'blocked' : ''}`}>{labels[reference.import_status]}</span>
    {reference.error && <p className="error-text">{reference.error.message} Paste an authorized excerpt instead.</p>}
    {reference.import_status === 'imported' && <>
      <p className="muted">{reference.import_permission?.model_use_allowed && reference.import_permission?.non_confidential ? 'Authorized for use as AI reference material.' : 'Excluded from AI prompts: model-use and non-confidential permissions are required.'}</p>
      <details><summary>Imported text</summary><p className="preserve-text">{reference.imported_text}</p></details>
    </>}
    {reference.import_status !== 'imported' && <details>
      <summary>Authorize an import</summary>
      <form onSubmit={importReference} aria-label={`Import ${reference.url}`}>
        <fieldset disabled={disabled}>
          <legend className="sr-only">Source permissions</legend>
          <p className="muted">Public visibility alone is not permission. This action fetches this page only if the backend permits it.</p>
          <label htmlFor={`basis-${reference.id}`}>Permission basis</label>
          <select id={`basis-${reference.id}`} value={basis} onChange={event => setBasis(event.target.value as SourcePermission['basis'])}>
            <option value="owned">I own this content</option><option value="public_domain">Public domain</option><option value="licensed">Licensed for this use</option><option value="explicit_permission">Explicit permission</option>
          </select>
          <label htmlFor={`evidence-${reference.id}`}>Permission details</label>
          <textarea id={`evidence-${reference.id}`} value={evidence} onChange={event => setEvidence(event.target.value)} maxLength={1000} rows={2} required />
          <label className="check"><input type="checkbox" checked={access} onChange={event => setAccess(event.target.checked)} required />I confirm automated access is permitted.</label>
          <label className="check"><input type="checkbox" checked={modelUse} onChange={event => setModelUse(event.target.checked)} />The source may be used in AI prompts.</label>
          <label className="check"><input type="checkbox" checked={nonConfidential} onChange={event => setNonConfidential(event.target.checked)} />The source is non-confidential.</label>
          <button type="submit" disabled={!access || !evidence.trim()}>Import authorized page</button>
        </fieldset>
      </form>
    </details>}
  </article>;
}

export function ReferencePanel({ references, disabled, onImport }: { references: References; disabled: boolean; onImport: (input: ImportInput) => Promise<void> }) {
  const count = references.examples.length + references.headlines.length + references.urls.length;
  return <section aria-labelledby="references-title" className="panel references">
    <h2 id="references-title">Saved references <span className="count">{count}/10</span></h2>
    {!count && <p className="muted">Add examples with your next message. Saved references stay with this chat.</p>}
    {references.examples.map((item, index) => <details key={item.id}><summary>Sample text {index + 1}</summary><p className="preserve-text">{item.text}</p></details>)}
    {!!references.headlines.length && <details><summary>Headlines ({references.headlines.length})</summary><ul>{references.headlines.map(item => <li key={item.id}>{item.text}</li>)}</ul></details>}
    {references.urls.map(reference => <ReferenceImport key={reference.id} reference={reference} disabled={disabled} onImport={onImport} />)}
  </section>;
}
