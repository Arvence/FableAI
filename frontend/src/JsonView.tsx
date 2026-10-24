import { useState } from 'react';

export function JsonView({ value }: { value: unknown }) {
  const [notice, setNotice] = useState('');
  const json = JSON.stringify(value, null, 2);

  async function copy() {
    try {
      await navigator.clipboard.writeText(json);
      setNotice('JSON copied.');
    } catch {
      setNotice('Clipboard unavailable. Select and copy the JSON below.');
    }
  }

  return <section className="json-view" aria-label="Response JSON">
    <div className="json-heading">
      <span>Response JSON</span>
      <span className="copy-status" role="status">{notice}</span>
      <button className="icon-button" type="button" onClick={copy} aria-label="Copy JSON" title="Copy JSON">
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
      </button>
    </div>
    <pre aria-label="Exact response JSON">{json}</pre>
  </section>;
}
