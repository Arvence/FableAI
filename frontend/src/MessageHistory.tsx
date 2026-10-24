import { useLayoutEffect, useRef } from 'react';
import Markdown from 'react-markdown';
import { JsonView } from './JsonView';
import type { Message } from './types';

export function MessageHistory({ messages, createdAt, loading }: { messages: Message[]; createdAt?: string; loading: boolean }) {
  const history = useRef<HTMLElement>(null);
  const followLatest = useRef(true);
  const firstLoad = useRef(true);
  const turns = new Map<string, Message[]>();
  for (const message of messages) {
    const turn = turns.get(message.submission_id) ?? [];
    turn.push(message);
    turns.set(message.submission_id, turn);
  }
  const lastMessage = messages.at(-1)?.message_id;
  const date = new Date(createdAt || messages[0]?.created_at || '');
  const hasDate = messages.length > 0 && !Number.isNaN(date.getTime());

  useLayoutEffect(() => {
    const container = history.current;
    const latest = container?.lastElementChild as HTMLElement | null;
    if (!container || !latest || !lastMessage) return;
    if (getComputedStyle(container).overflowY === 'auto' && (firstLoad.current || followLatest.current)) {
      container.scrollTop += latest.getBoundingClientRect().top - container.getBoundingClientRect().top - 12;
    }
    firstLoad.current = false;
  }, [lastMessage]);

  function trackScroll() {
    const container = history.current;
    if (container) followLatest.current = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
  }

  return <section ref={history} className="message-history" tabIndex={0} aria-label="Message history" onScroll={trackScroll}>
    {!messages.length && <div className="empty-chat"><h2>{loading ? 'Loading conversation…' : 'What would you like to create?'}</h2></div>}
    {hasDate && <div className="conversation-date"><time aria-label="Conversation date" dateTime={date.toISOString()}>{date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })}</time></div>}
    {[...turns].map(([id, turn], index) => <section className="conversation-turn" key={id} aria-label={'Request and response ' + (index + 1)}>
      {turn.map(message => <article key={message.message_id} className={'message ' + message.role} aria-label={message.role === 'user' ? 'You message' : 'FableAI message'}>
        <div className={'message-content' + (message.response?.kind === 'error' ? ' error-text' : '')}>
          <Markdown components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>, img: ({ alt }) => <span>{alt}</span> }}>{message.content}</Markdown>
        </div>
        {message.request && (message.request.examples.length + message.request.headlines.length + message.request.source_urls.length > 0) && <details><summary>Submitted references</summary>
          {message.request.examples.map((text, index) => <p className="preserve-text" key={'sample-' + index}><strong>Sample {index + 1}: </strong>{text}</p>)}
          {message.request.headlines.map((text, index) => <p key={'headline-' + index}><strong>Headline: </strong>{text}</p>)}
          {message.request.source_urls.map((url, index) => <p className="url-text" key={'url-' + index}><strong>URL: </strong>{url}</p>)}
        </details>}
        {message.response && <JsonView value={message.response} />}
      </article>)}
      {!turn.some(message => message.role === 'assistant') && <p className="awaiting-response">No saved response yet.</p>}
    </section>)}
  </section>;
}
