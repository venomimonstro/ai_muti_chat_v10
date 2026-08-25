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
  model_version: string | null;
  exact_api_id: string;
  cost_rub: string | null;
  input_tokens: number;
  output_tokens: number;
  error_code: string;
  correlation_id: string;
  completed_at: string | null;
  context: {
    memories: Array<{id: string; scope: string; memory_type: string; content: string}>;
    memory_action: {action: string; message: string} | null;
    version: number | null;
    sha256: string;
    budget: {
      context_window?: number;
      input_limit?: number;
      input_tokens?: number;
      output_reserved?: number;
      remaining?: number;
    };
    components: Array<{
      kind: string;
      source_id: string;
      label: string;
      content: string;
      tokens: number;
      score: number;
      truncated: boolean;
      citation?: {
        id: string;
        file_id: string;
        file_name: string;
        chunk_id: string;
        position: number;
        source_location: Record<string, string | number>;
        project_id: string;
        content_sha256: string;
      };
    }>;
    citations: Array<{
      id: string;
      file_id: string;
      file_name: string;
      chunk_id: string;
      position: number;
      source_location: Record<string, string | number>;
      project_id: string;
      content_sha256: string;
    }>;
    dropped_or_deduplicated: number;
    routing: {
      decision_id: string;
      mode: "manual" | "economy" | "balanced" | "maximum";
      task_taxonomy: string;
      selected_model: string;
      model_version: string | null;
      exact_api_id: string;
      explanation: string;
      policy_version: string;
      classification_confidence: number;
      required_capabilities: string[];
      estimated_cost_rub: string;
      candidates: Array<{
        model: string;
        provider: string;
        model_version: string | null;
        exact_api_id: string;
        status: "eligible" | "rejected";
        reasons: string[];
        estimated_cost_rub: string | null;
        quality?: number;
        score?: number | null;
        rank?: number;
      }>;
    } | null;
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
  routing_mode: "manual" | "economy" | "balanced" | "maximum";
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
  model_version: string | null;
  exact_api_id: string;
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
  auto_memory_enabled: boolean;
  auto_memory_default_scope: "global" | "project" | "conversation";
  auto_memory_available: boolean;
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
  trust_level: string;
  source_kind: string;
  status: "active" | "archived" | "superseded" | "deleted";
  pinned: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type MemoryCandidate = {
  id: string;
  project: string | null;
  conversation: string;
  source_message: string;
  suggested_scope: "global" | "project" | "conversation";
  memory_type: "fact" | "preference" | "instruction" | "decision";
  content: string;
  subject_key: string;
  confidence_score: string;
  trust_level: string;
  source_kind: string;
  extraction_version: string;
  reason: string;
  status: "pending" | "conflict" | "duplicate" | "accepted" | "rejected" | "dismissed";
  duplicate_content: string | null;
  conflict_content: string | null;
  accepted_item: string | null;
  created_at: string;
  reviewed_at: string | null;
};

export type SearchResult = {
  type: "conversation" | "message" | "project" | "file";
  id: string;
  conversation_id?: string;
  project_id?: string;
  title: string;
  excerpt: string;
  role?: "user" | "assistant" | "system";
  created_at: string;
  score: number;
  match: "keyword" | "semantic" | "hybrid";
  navigation: {
    conversation_id?: string;
    message_id?: string;
    project_id?: string;
    file_id?: string;
    anchor?: string;
  };
  signals?: {lexical: number; semantic: number};
};
