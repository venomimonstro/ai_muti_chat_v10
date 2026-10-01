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
  cost_breakdown?: {
    llm_rub: string;
    search_rub: string;
    total_rub: string;
  };
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
      mode: "auto" | "manual" | "economy" | "balanced" | "maximum";
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
  branch: string | null;
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
  routing_mode: "auto" | "manual" | "economy" | "balanced" | "maximum";
  project: string | null;
  memory_enabled: boolean;
  active_branch: string | null;
  branches: Array<{
    id: string;
    parent: string | null;
    forked_from: string | null;
    title: string;
    created_at: string;
  }>;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
};

export type CompareRun = {
  id: string;
  conversation_id: string;
  branch_id: string | null;
  source_message_id: string | null;
  prompt: string;
  state: "previewed" | "running" | "completed" | "partial" | "failed";
  models: string[];
  expected_min_rub: string;
  expected_max_rub: string;
  actual_cost_rub: string;
  synthesis_model: string;
  synthesis_output: string;
  synthesis_cost_rub: string;
  variants: Array<{
    id: string;
    model: string;
    model_name: string;
    provider: string;
    state: "queued" | "running" | "completed" | "failed";
    output: string;
    expected_min_rub: string;
    expected_max_rub: string;
    actual_cost_rub: string;
    input_tokens: number;
    output_tokens: number;
    latency_ms: number | null;
    error_code: string;
  }>;
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
  routing_tiers?: Array<"economy" | "balanced" | "maximum">;
  routing_tiers_configured?: boolean;
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
    available_delta_rub: string;
    reserved_delta_rub: string;
    paid_delta_rub: string;
    promo_delta_rub: string;
    source_type: string;
    source_id: string;
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