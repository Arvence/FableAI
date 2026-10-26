import type { ApiErrorResponse, ChatResponse, Conversation, ConversationSummary, Entry, ImportResponse, Message, PendingSubmission } from './types';

export const base = '/api/v1/conversations';

export class ApiError extends Error {
  constructor(message: string, public response?: ApiErrorResponse) {
    super(message);
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(base + path, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('Cannot reach the local backend. Check that it is running, then try again.');
  }
  if (response.ok && response.status === 204) return undefined as T;
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError('The backend did not return JSON. Check the local server connection.');
  }
  if (!response.ok) {
    const failure = data as ApiErrorResponse;
    if (failure?.kind === 'error' && typeof failure.error?.message === 'string') {
      throw new ApiError(failure.error.message, failure);
    }
    throw new ApiError(`The request failed (HTTP ${response.status}).`);
  }
  const submissionId = new Headers(options.headers).get('Idempotency-Key');
  if (submissionId && response.headers.get('Idempotency-Key') !== submissionId) {
    throw new ApiError('The response did not match this submission. Retry the saved request to recover its result.');
  }
  return data as T;
}

export function listConversations(offset = 0, signal?: AbortSignal) {
  return request<{ items: ConversationSummary[]; next_offset: number | null }>(`?limit=50&offset=${offset}`, { signal });
}

export async function loadConversation(id: string, signal?: AbortSignal) {
  const detail = await request<Conversation>(`/${id}`, { signal });
  if (detail.conversation_id !== id) throw new ApiError('The response belongs to a different conversation. Reload this chat.');
  const messages: Message[] = [];
  let after: number | null = 0;
  while (after !== null) {
    const page: { items: Message[]; next_after: number | null } = await request(`/${id}/messages?limit=100&after=${after}`, { signal });
    messages.push(...page.items);
    after = page.next_after;
  }
  return { detail, messages };
}

export async function submit(pending: PendingSubmission) {
  const result = await request<ChatResponse | ImportResponse>(`/${pending.conversationId}/${pending.operation}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pending.key },
    body: JSON.stringify(pending.body),
  });
  if (result.conversation_id !== pending.conversationId) throw new ApiError('The response belongs to a different conversation. Retry the saved request to recover its result.');
  return result;
}

export function review(id: string, entry: Entry, status: 'accepted' | 'rejected') {
  return request<Entry>(`/${id}/entries/${entry.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_version: entry.version, status }),
  });
}
