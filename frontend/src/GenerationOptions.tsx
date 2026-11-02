import { categories, defaultWritingSettings, generationSettingsError, languages, lengths, toGenerationSettings, tones } from './writingSettings';
import type { WritingSettings } from './writingSettings';

export function GenerationOptions({ settings, disabled, onChange, onClose }: { settings: WritingSettings; disabled: boolean; onChange: (settings: WritingSettings) => void; onClose?: () => void }) {
  const countError = generationSettingsError(toGenerationSettings(settings));
  return <section id="generation-options" className="generation-options">
    <div className="generation-options-heading">
      <h2 id="writing-options-title">Writing Options</h2>
      <button type="button" className="options-reset" disabled={disabled} onClick={() => onChange(defaultWritingSettings())}>Reset</button>
      {onClose && <button type="button" className="icon-button" onClick={onClose} aria-label="Close writing options" title="Close writing options">
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18" /></svg>
      </button>}
    </div>
    <fieldset disabled={disabled} className="generation-options-fields">
      <legend className="sr-only">Writing settings</legend>
      <div className="writing-field">
        <label htmlFor="content-type">Content type</label>
        <select id="content-type" value={settings.category} onChange={event => onChange({ ...settings, category: event.target.value as WritingSettings['category'] })}>
          {categories.map(category => <option key={category} value={category}>{category[0].toUpperCase() + category.slice(1)}</option>)}
        </select>
      </div>
      <div className="writing-field">
        <label htmlFor="writing-tone">Tone</label>
        <select id="writing-tone" value={settings.tone} onChange={event => onChange({ ...settings, tone: event.target.value as WritingSettings['tone'] })}>
          {tones.map(tone => <option key={tone} value={tone}>{tone[0].toUpperCase() + tone.slice(1)}</option>)}
        </select>
      </div>
      <div className="writing-field">
        <label htmlFor="writing-language">Language</label>
        <select id="writing-language" value={settings.language} onChange={event => onChange({ ...settings, language: event.target.value })}>
          {languages.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </div>
      <div className="writing-field">
        <label htmlFor="body-length">Body length</label>
        <select id="body-length" value={settings.max_body_chars} onChange={event => {
          const length = lengths.find(value => value.max === Number(event.target.value));
          if (length) onChange({ ...settings, min_body_chars: length.min, max_body_chars: length.max });
        }}>
          {lengths.map(length => <option key={length.max} value={length.max} disabled={Number(settings.count) * length.max > 12000}>{length.label}</option>)}
        </select>
      </div>
      <div className="draft-count-field">
        <label htmlFor="draft-count">Drafts</label>
        <input id="draft-count" type="number" min="1" max="20" step="1" inputMode="numeric" required value={settings.count} onChange={event => onChange({ ...settings, count: event.target.value })} aria-invalid={!!countError} aria-describedby={countError ? 'draft-count-error' : undefined} />
        {countError && <p id="draft-count-error" className="error-text" role="alert">{countError}</p>}
      </div>
    </fieldset>
    <p>These settings are sent with every message. Clear writing instructions can override them.</p>
    <p>Body length is an approximate target. Explicit character limits remain strict.</p>
  </section>;
}
