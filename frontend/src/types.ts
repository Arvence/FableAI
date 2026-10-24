export interface ApiErrorResponse {
  kind: 'error';
  conversation_id: string | null;
  message_id: string | null;
  error: { code: string; message: string; details: { field: string; message: string }[] };
}

export interface Entry {
  id: string;
  version: number;
  title: string;
  body: string;
  category: 'news' | 'announcement';
  tags: string[];
  language: string;
  is_synthetic: true;
  status: 'draft' | 'accepted' | 'rejected';
}

export type ChatResponse = ApiErrorResponse | {
  kind: 'message' | 'clarification' | 'generation' | 'revision';
  conversation_id: string;
  message_id: string;
  assistant_message: string;
  entries: Entry[];
};

export interface SourcePermission {
  url: string;
  basis: 'owned' | 'public_domain' | 'licensed' | 'explicit_permission';
  evidence: string;
  automated_access_allowed: boolean;
  model_use_allowed: boolean;
  non_confidential: boolean;
}

export interface URLReference {
  id: string;
  url: string;
  import_status: 'not_requested' | 'pending' | 'imported' | 'failed';
  imported_text: string | null;
  import_permission: SourcePermission | null;
  error: ApiErrorResponse['error'] | null;
}

export interface References {
  examples: { id: string; text: string }[];
  headlines: { id: string; text: string }[];
  urls: URLReference[];
}

export interface ConversationSummary {
  conversation_id: string;
  created_at: string;
  updated_at: string;
  revision: number;
  processing: boolean;
}

export interface Conversation extends ConversationSummary {
  references: References;
  entries: Entry[];
}

export interface Message {
  message_id: string;
  submission_id: string;
  sequence: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  request: ChatInput | null;
  response: ChatResponse | null;
}

export interface ChatInput {
  instruction: string;
  examples: string[];
  headlines: string[];
  source_urls: string[];
  revision_targets: { entry_id: string; expected_version: number }[];
}

export interface ImportInput {
  reference_id: string;
  confirmed: true;
  permissions: SourcePermission[];
}

export interface ImportResponse {
  kind: 'reference_import';
  conversation_id: string;
  references: References;
}

export type PendingSubmission = { key: string; conversationId: string } & (
  { operation: 'messages'; body: ChatInput } | { operation: 'references/import'; body: ImportInput }
);
