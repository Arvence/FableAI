import { readSession, writeSession } from './session';
import type { GenerationSettings } from './types';

export const defaultSettings: Required<GenerationSettings> = { body_length_mode: 'target', count: 1, tone: 'neutral', category: 'news', language: 'en', min_body_chars: 100, max_body_chars: 300 };
export type WritingSettings = Omit<Required<GenerationSettings>, 'count'> & { count: string };

export const categories = ['news', 'announcement', 'update', 'article', 'blog'] as const;
export const tones = ['neutral', 'formal', 'informative', 'playful'] as const;
export const languages = [['en', 'English'], ['tr', 'Turkish'], ['de', 'German'], ['fr', 'French'], ['es', 'Spanish']];
export const lengths = [
  { label: 'Short (~100-300 chars)', min: 100, max: 300 },
  { label: 'Medium (~300-700 chars)', min: 300, max: 700 },
  { label: 'Long (~700-1,200 chars)', min: 700, max: 1200 },
];

export function defaultWritingSettings(): WritingSettings {
  return { ...defaultSettings, count: String(defaultSettings.count) };
}

export function restoreSettings(conversationId: string): WritingSettings {
  const saved = readSession<Omit<Partial<WritingSettings>, 'count'> & { count?: unknown } | null>('settings:' + conversationId, null);
  const settings = defaultWritingSettings();
  if (!saved || typeof saved !== 'object') return settings;
  if (typeof saved.count === 'number' || typeof saved.count === 'string') settings.count = String(saved.count);
  if (saved.tone && tones.includes(saved.tone)) settings.tone = saved.tone;
  if (saved.category && categories.includes(saved.category)) settings.category = saved.category;
  if (saved.language && languages.some(([code]) => code === saved.language)) settings.language = saved.language;
  const length = lengths.find(value => value.min === saved.min_body_chars && value.max === saved.max_body_chars);
  if (length) {
    settings.min_body_chars = length.min;
    settings.max_body_chars = length.max;
  }
  return settings;
}

export function saveSettings(conversationId: string, settings: WritingSettings): void {
  const parsed = toGenerationSettings(settings).count;
  const count = Number.isInteger(parsed) && parsed >= 1 && parsed <= 20 ? parsed : settings.count;
  writeSession('settings:' + conversationId, { ...settings, count });
}

export function toGenerationSettings(settings: WritingSettings): Required<GenerationSettings> {
  return { ...settings, count: /^\d+$/.test(settings.count) ? Number(settings.count) : Number.NaN };
}

export function generationSettingsError(settings?: GenerationSettings): string {
  if (settings === undefined) return '';
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return 'Choose valid writing options before sending.';
  if ('count' in settings && (typeof settings.count !== 'number' || !Number.isInteger(settings.count) || settings.count < 1 || settings.count > 20)) return 'Enter a whole number from 1 to 20.';
  if (settings.count !== undefined && settings.max_body_chars !== undefined && settings.count * settings.max_body_chars > 12000) return 'Reduce Drafts or Body length to stay within 12,000 characters per request.';
  return '';
}
