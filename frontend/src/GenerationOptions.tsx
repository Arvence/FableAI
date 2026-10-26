import type { GenerationSettings } from './types';
import { readSession } from './session';

export const defaultSettings: Required<GenerationSettings> = { body_length_mode: 'target', count: 1, tone: 'neutral', category: 'news', language: 'en', min_body_chars: 100, max_body_chars: 300 };

const categories = ['news', 'announcement', 'update', 'article', 'blog'] as const;
const tones = ['neutral', 'formal', 'informative', 'playful'] as const;
const languages = [['en', 'English'], ['tr', 'Turkish'], ['de', 'German'], ['fr', 'French'], ['es', 'Spanish']];
const lengths = [
  { label: 'Short (~100-300 chars)', min: 100, max: 300 },
  { label: 'Medium (~300-700 chars)', min: 300, max: 700 },
  { label: 'Long (~700-1,200 chars)', min: 700, max: 1200 },
];

export function restoreSettings(conversationId: string): Required<GenerationSettings> {
  const saved = readSession<GenerationSettings | null>('settings:' + conversationId, null);
  if (!saved || typeof saved !== 'object') return { ...defaultSettings };
  const settings = { ...defaultSettings };
  if (typeof saved.count === 'number' && Number.isInteger(saved.count) && saved.count >= 1 && saved.count <= 20) settings.count = saved.count;
  if (saved.tone && tones.includes(saved.tone)) settings.tone = saved.tone;
  if (saved.category && categories.includes(saved.category)) settings.category = saved.category;
  if (saved.language && languages.some(([code]) => code === saved.language)) settings.language = saved.language;
  const length = lengths.find(value => value.min === saved.min_body_chars && value.max === saved.max_body_chars);
  if (length && settings.count * length.max <= 12000) {
    settings.min_body_chars = length.min;
    settings.max_body_chars = length.max;
  }
  return settings;
}

export function GenerationOptions({ settings, onChange }: { settings: Required<GenerationSettings>; onChange: (settings: Required<GenerationSettings>) => void }) {
  return <div id="generation-options" className="generation-options">
    <div className="generation-options-heading">
      <span>Writing options</span>
      <button type="button" className="options-reset" onClick={() => onChange({ ...defaultSettings })}>Reset</button>
    </div>
    <div className="generation-options-fields">
      <label>Content type
        <select value={settings.category} onChange={event => onChange({ ...settings, category: event.target.value as Required<GenerationSettings>['category'] })}>
          {categories.map(category => <option key={category} value={category}>{category[0].toUpperCase() + category.slice(1)}</option>)}
        </select>
      </label>
      <label>Tone
        <select value={settings.tone} onChange={event => onChange({ ...settings, tone: event.target.value as Required<GenerationSettings>['tone'] })}>

          {tones.map(tone => <option key={tone} value={tone}>{tone[0].toUpperCase() + tone.slice(1)}</option>)}
        </select>
      </label>
      <label>Language
        <select value={settings.language} onChange={event => onChange({ ...settings, language: event.target.value })}>

          {languages.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </label>
      <label>Body length
        <select value={settings.max_body_chars} onChange={event => {
          const length = lengths.find(value => value.max === Number(event.target.value));
          if (length) onChange({ ...settings, min_body_chars: length.min, max_body_chars: length.max });
        }}>

          {lengths.map(length => <option key={length.max} value={length.max} disabled={settings.count * length.max > 12000}>{length.label}</option>)}
        </select>
      </label>
      <label>Drafts
        <select value={settings.count} onChange={event => onChange({ ...settings, count: Number(event.target.value) })}>

          {Array.from({ length: 20 }, (_, index) => index + 1).map(count => <option key={count} value={count} disabled={count * settings.max_body_chars > 12000}>{count}</option>)}
        </select>
      </label>
    </div>
    <p>Body length is an approximate target. Drafts outside it are kept with a length notice. Explicit character limits in your message remain strict.</p>
  </div>;
}
