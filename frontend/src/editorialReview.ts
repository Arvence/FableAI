import type { Entry } from './types';

export interface EditorialFinding {
  code: 'topic_coverage' | 'headline' | 'repetition' | 'filler' | 'genre' | 'perspective' | 'explicit_wording' | 'explicit_format';
  message: string;
}

const topicStopWords = new Set('a an the about and or of for to in on with our your my some new recent latest fictional mock game news announcement update article blog story content please create write generate describe'.split(' '));
const filler = /\b(?:deep within|digital expanses|imaginary digital realm|rich tapestry|it is important to note|truly remarkable|vast and sprawling|ever.evolving landscape)\b/gi;
const creativeStyle = /\b(?:fantasy (?:story|narrative)|fairy tale|poem|poetry|lyrical|flowery|ornate|purple prose)\b/i;

function words(value: string): string[] {
  return value.normalize('NFKC').toLocaleLowerCase('en').match(/[\p{L}\p{N}]+/gu) ?? [];
}

function phraseCount(text: string, phrase: string): number {
  const normalized = ' ' + words(text).join(' ') + ' ';
  const target = ' ' + words(phrase).join(' ') + ' ';
  if (target === '  ') return 0;
  let count = 0;
  let start = 0;
  while ((start = normalized.indexOf(target, start)) >= 0) {
    count += 1;
    start += target.length - 1;
  }
  return count;
}

function repeatsSentences(body: string): boolean {
  const sentences = (body.match(/[^.!?\n]+/g) ?? []).map(words).filter(sentence => sentence.length >= 6);
  return sentences.some((sentence, index) => sentences.slice(0, index).some(previous => {
    const first = new Set(sentence);
    const second = new Set(previous);
    const shared = [...first].filter(word => second.has(word)).length;
    return shared / new Set([...first, ...second]).size >= 0.8;
  }));
}

export function reviewWriting(entry: Pick<Entry, 'title' | 'body' | 'category' | 'language'>, instruction = ''): EditorialFinding[] {
  const findings: EditorialFinding[] = [];
  const title = entry.title.replace(/^\[FICTIONAL\]\s*/, '');
  const text = title + '\n' + entry.body;
  const english = entry.language.toLowerCase().split('-')[0] === 'en';
  const creative = creativeStyle.test(instruction);
  const subject = instruction.match(/\babout\s+([^.!?\n;]+)/i)?.[1];
  const topicWords = words(subject ?? '').filter(word => !topicStopWords.has(word));
  const outputWords = new Set(words(text));
  if (topicWords.length && !topicWords.some(word => outputWords.has(word))) {
    findings.push({ code: 'topic_coverage', message: 'The requested subject may be missing. Check the draft against your message.' });
  }

  const wording = /\b(?:(do not|don't|never|avoid)\s+)?(?:mention(?:ing)?|includ(?:e|ing)|us(?:e|ing))\s+["“]([^"”\n]{1,100})["”](?:\s+(at least|exactly)\s+(once|twice|\d+\s+times?))?/gi;
  for (const match of instruction.matchAll(wording)) {
    const count = phraseCount(text, match[2]);
    const numericCount = parseInt(match[4] ?? '', 10);
    const required = match[4]?.toLowerCase() === 'twice' ? 2 : Number.isNaN(numericCount) ? 1 : numericCount;
    const unmet = match[1] ? count > 0 : match[3]?.toLowerCase() === 'exactly' ? count !== required : count < required;
    if (unmet) {
      findings.push({ code: 'explicit_wording', message: 'A quoted wording requirement may be unmet. Review the requested phrase and its count.' });
      break;
    }
  }

  const bullets = /^\s*(?:[-*]|\d+[.)])\s+/m.test(entry.body);
  const noBullets = /\b(?:no|without|avoid|do not use|don't use)\s+bullet(?:s| points| list)?\b/i.test(instruction);
  const wantsBullets = /\b(?:use|include|write (?:it )?as|format (?:it )?as)\s+(?:a )?bullet(?:s| points| list)\b/i.test(instruction);
  if ((noBullets && bullets) || (!noBullets && wantsBullets && !bullets)) {
    findings.push({ code: 'explicit_format', message: 'The bullet-list format may not match your instruction.' });
  }

  if (english && (words(title).length < 3 || /^(?:(?:a|an|the)\s+)?(?:(?:new|exciting|important|latest|interesting|major|amazing|big)\s+)*(?:news|announcement|update|article|blog|story|developments?)(?:\s+(?:about|for|in|on)\b.*)?$/i.test(title))) {
    findings.push({ code: 'headline', message: 'The headline may be too general. Consider naming the subject and the main development.' });
  }
  if (!/\b(?:refrain|repeat (?:the |each )?sentence|intentional repetition)\b/i.test(instruction) && repeatsSentences(entry.body)) {
    findings.push({ code: 'repetition', message: 'Some sentences repeat similar wording. Check whether each adds new information.' });
  }
  const report = ['news', 'announcement', 'update'].includes(entry.category);
  const framingCount = (entry.body.match(/\b(?:fictional|imaginary|invented|make-believe)\b/gi) ?? []).length;
  if (english && !creative && ((entry.body.match(filler) ?? []).length >= 2 || (report && framingCount >= 3))) {
    findings.push({ code: 'filler', message: 'Repeated fictional framing or stock descriptions may add padding. Keep details that help the reader.' });
  }
  if (english && report && !creative && /^(?:deep within|once upon a time|in the heart of|far beyond)\b/i.test(entry.body.trim())) {
    findings.push({ code: 'genre', message: 'The opening reads like scene-setting narration. Check whether it suits the selected content type.' });
  }
  const characterView = /(?:^|[.!?]\s+)(?:he|she|they)\s+(?:is|are|was|were|needs?|waits?|seeks?|pleads?)\b/i.test(entry.body);
  const playerView = /\b(?:players?\s+(?:can|will|must|may|need to)|(?:virtual|digital)\s+(?:world|realm)|gameplay)\b/i.test(entry.body);
  const requestedMix = /\b(?:mix|switch|contrast|compare|both)\b.{0,60}\b(?:perspectives?|viewpoints?|players?|characters?)\b/i.test(instruction);
  if (english && report && !creative && !requestedMix && characterView && playerView) {
    findings.push({ code: 'perspective', message: 'The draft may switch between a character viewpoint and player commentary. Check that this is intentional.' });
  }
  return findings;
}
