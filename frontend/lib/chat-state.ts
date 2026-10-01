import type {ChatMessage} from "./types";

export const isOptimisticMessage = (message: ChatMessage) =>
  message.id.startsWith("local-user-") || message.id.startsWith("local-ai-");

export function snapshotMessageUpdate(data: Record<string, unknown>): Pick<ChatMessage, "content" | "status"> {
  const content = String(data.text ?? "");
  const state = String(data.state ?? "");
  return {content, status: state === "completed" ? "completed" : ["failed", "cancelled"].includes(state) ? (content ? "partial" : "failed") : "streaming"};
}

// Identity, rather than text or timestamps, associates browser bubbles with
// durable messages. Long preflight and repeated identical prompts are valid.
export function mergeChatMessages(previous: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const messages = new Map(previous.map(message => [message.id, message]));
  for (const message of incoming) messages.set(message.id, message);
  const durable = [...messages.values()].filter(message => !isOptimisticMessage(message));
  return [...messages.values()].filter(message => {
    if (!isOptimisticMessage(message)) return true;
    return !durable.some(server => server.role === message.role && (
      message.role === "user"
        ? Boolean(message.client_message_id && server.client_message_id === message.client_message_id)
        : Boolean(message.pending_generation_id && server.generation?.id === message.pending_generation_id)
    ));
  }).sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at));
}
