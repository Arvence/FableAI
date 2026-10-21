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

  return <details className="json-view">
    <summary>View response JSON</summary>
    <div className="toolbar"><button type="button" onClick={copy}>Copy JSON</button><span role="status">{notice}</span></div>
    <pre tabIndex={0} aria-label="Exact response JSON">{json}</pre>
  </details>;
}
