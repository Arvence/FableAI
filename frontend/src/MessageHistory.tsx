import { useEffect, useRef } from 'react';
import { JsonView } from './JsonView';
import type { Message } from './types';

export function MessageHistory({ messages }: { messages: Message[] }) {
  const history = useRef<HTMLElement>(null);
  useEffect(() => {
    if (history.current) history.current.scrollTop = history.current.scrollHeight;
  }, [messages.length]);

  return <section ref={history} className="message-history" tabIndex={0} aria-label="Message history">
    {!messages.length && <div className="empty-chat"><h2>Start with an idea and a few examples</h2><p>Describe the fictional content you want. FableAI can ask questions, create drafts, and revise selected entries.</p></div>}
    {messages.map(message => <article key={message.message_id} className={`message ${message.role}`} aria-label={`${message.role === 'user' ? 'You' : 'FableAI'} message`}>
      <div className="message-meta"><strong>{message.role === 'user' ? 'You' : 'FableAI'}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></div>
      <p className={`preserve-text ${message.response?.kind === 'error' ? 'error-text' : ''}`}>{message.content}</p>
      {message.request && (message.request.examples.length + message.request.headlines.length + message.request.source_urls.length > 0) && <details><summary>Submitted references</summary>
        {message.request.examples.map((text, index) => <p className="preserve-text" key={`sample-${index}`}><strong>Sample {index + 1}: </strong>{text}</p>)}
        {message.request.headlines.map((text, index) => <p key={`headline-${index}`}><strong>Headline: </strong>{text}</p>)}
        {message.request.source_urls.map((url, index) => <p className="url-text" key={`url-${index}`}><strong>URL: </strong>{url}</p>)}
      </details>}
      {message.response && <JsonView value={message.response} />}
    </article>)}
  </section>;
}
