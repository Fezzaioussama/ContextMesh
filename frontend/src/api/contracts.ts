export interface AgentMetadata {
  name: string;
  mode: "provider_chat";
  provider: string;
  model: string;
  configured: boolean;
  retrieval_enabled: false;
  limits: {
    max_message_chars: number;
    max_history_messages: number;
    max_output_tokens: number;
  };
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  turn_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface CompletedTurn {
  turn_id: string;
  conversation_id: string;
  mode: "provider_chat";
  user_message: Message;
  assistant_message: Message;
  usage: { input_tokens: number; output_tokens: number };
  trace: { stage: string; summary: string }[];
}

export interface SafeErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    retryable: boolean;
  };
}
