export type User = {
  id: string;
  username: string;
  email: string;
  role: string;
  status: string;
};

export type GenerationMeta = {
  id: string;
  state: string;
  model: string;
  provider: string;
  cost_rub: string | null;
  input_tokens: number;
  output_tokens: number;
  error_code: string;
  correlation_id: string;
  completed_at: string | null;
  context: {
    memories: Array<{id: string; scope: string; memory_type: string; content: string}>;
    memory_action: {action: string; message: string} | null;
  };
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status: "saved" | "streaming" | "completed" | "partial" | "failed";
  generation: GenerationMeta | null;
  created_at: string;
};

export type Conversation = {
  id: string;
  title: string;
  selected_model: string;
  project: string | null;
  memory_enabled: boolean;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
};

export type AIModel = {
  slug: string;
  display_name: string;
  provider: string;
  capabilities: string[];
  context_window: number;
  max_output_tokens: number;
  available: boolean;
  health_state: string;
  price: {
    version: string;
    input_rub_per_million: string;
    output_rub_per_million: string;
    markup_percent: string;
  } | null;
};

export type Wallet = {
  available_rub: string;
  reserved_rub: string;
  paid_rub: string;
  promo_rub: string;
  entries: Array<{
    id: string;
    kind: string;
    amount_rub: string;
    created_at: string;
  }>;
};

export type Project = {
  id: string;
  name: string;
  description: string;
  active_instruction: string;
  role: "owner" | "editor" | "viewer";
  archived_at: string | null;
  updated_at: string;
};

export type FileAsset = {
  id: string;
  project: string;
  original_name: string;
  detected_type: string;
  size_bytes: number;
  status: string;
  error_code: string;
  extracted_chars: number;
  created_at: string;
};

export type Notification = {
  id: string;
  title: string;
  body: string;
  level: "info" | "warning" | "success";
  action_url: string;
  read_at: string | null;
  created_at: string;
};

export type Preference = {
  low_balance_threshold_rub: string;
  daily_spend_limit_rub: string | null;
  monthly_spend_limit_rub: string | null;
  product_notifications: boolean;
  billing_notifications: boolean;
  compact_sidebar: boolean;
  memory_enabled: boolean;
  updated_at: string;
};

export type MemoryItem = {
  id: string;
  project: string | null;
  conversation: string | null;
  scope: "global" | "project" | "conversation";
  memory_type: "fact" | "preference" | "instruction" | "decision";
  content: string;
  importance_score: string;
  confidence_score: string;
  source_kind: string;
  status: "active" | "archived" | "superseded" | "deleted";
  pinned: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type SearchResult = {
  type: "conversation" | "message" | "project" | "file";
  id: string;
  conversation_id?: string;
  project_id?: string;
  title: string;
  excerpt: string;
};
