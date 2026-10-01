import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = name => readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
function load(name, globals = {}) {
  const context = vm.createContext({exports: {}, process: {env: {}}, Headers, Response, FormData, TextDecoder, AbortController, DOMException, ...globals});
  vm.runInContext(ts.transpileModule(source(name), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, context);
  return context.exports;
}
const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
function stream(text, close = true) {
  return new Response(new ReadableStream({start(controller) {controller.enqueue(new TextEncoder().encode(text)); if (close) controller.close();}}));
}
function harness(open, fast = false) {
  const storage = new Map(); const sent = []; let cancellations = 0;
  const api = load("api", {
    window: {setTimeout: (fn, ms) => setTimeout(fn, fast && [20000, 30000].includes(ms) ? 10 : fast && ms <= 8000 ? 1 : ms), clearTimeout, confirm: () => true, location: {search: ""}},
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    sessionStorage: {getItem: () => null},
    fetch: async (url, init = {}) => {
      if (url.endsWith("/auth/csrf/")) return Response.json({csrf_token: "csrf"});
      if (url.endsWith("/messages/preview/")) return Response.json({confirmation_required: false});
      if (url.endsWith("/messages/cancel/")) {cancellations++; return Response.json({state: "cancelled"});}
      sent.push(init); return open(sent.length, init);
    },
  });
  const events = []; const controller = new AbortController();
  const run = callback => api.streamMessage("conversation", {content: "Запрос", client_message_id: "client-id"}, "same-operation", event => {events.push(event); callback?.(event);}, controller.signal);
  return {run, events, sent, controller, storage, cancellations: () => cancellations};
}

test("Stop after headers cancels a silent body and sends exactly one durable cancellation", async () => {
  const h = harness(() => stream(frame("generation", {id: "generation"}), false));
  await assert.rejects(h.run(event => {if (event.event === "generation") setImmediate(() => h.controller.abort());}), error => error.name === "AbortError");
  assert.equal(h.sent[0].signal.aborted, true);
  assert.equal(h.cancellations(), 1);
});

test("An idle stream reconnects with the original key instead of hanging or starting a new operation", async () => {
  const h = harness(attempt => attempt === 1 ? stream(frame("heartbeat", {}), false) : stream(frame("snapshot", {state: "completed", text: "Сохранённый ответ"})), true);
  await h.run();
  assert.equal(h.sent.length, 2);
  assert.ok(h.sent.every(request => request.headers.get("Idempotency-Key") === "same-operation"));
  assert.equal(h.events.at(-1).data.text, "Сохранённый ответ");
  assert.equal(h.cancellations(), 0);
});

test("A completed event finishes without waiting for the socket to close and removes abort listeners", async () => {
  const h = harness(() => stream(frame("generation", {id: "generation"}) + frame("completed", {cost_rub: "0.50"}), false));
  await h.run(); h.controller.abort(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.cancellations(), 0);
  assert.equal(h.storage.size, 0);
});

test("A broken SSE frame reconnects to the same operation and replays an authoritative snapshot", async () => {
  const h = harness(attempt => attempt === 1 ? stream("event: delta\ndata: {broken\n\n") : stream(frame("generation", {id: "generation"}) + frame("snapshot", {state: "completed", text: "Ответ"})), true);
  await h.run(); assert.equal(h.sent.length, 2);
  assert.equal(h.events.find(event => event.event === "generation").data.client_message_id, "client-id");
});

test("CRLF event framing is accepted", async () => {
  const h = harness(() => stream(frame("completed", {}).replaceAll("\n", "\r\n")));
  await h.run(); assert.equal(h.sent.length, 1);
});

const state = load("chat-state");
const message = (id, changes = {}) => ({id, role: "assistant", content: "Ответ", status: "streaming", created_at: "2026-10-01T18:00:00Z", generation: null, ...changes});
test("Long preflight and different response text cannot leave duplicate assistant bubbles", () => {
  const local = message("local-ai-1", {pending_generation_id: "generation"});
  const server = message("server", {content: "Полный ответ", created_at: "2026-10-01T18:01:00Z", generation: {id: "generation"}});
  const merged = state.mergeChatMessages([local], [server]);
  assert.equal(merged.length, 1); assert.equal(merged[0].id, "server");
});

test("Identical prompts in different requests are matched by client identity, not text", () => {
  const local = message("local-user-1", {role: "user", client_message_id: "second"});
  const previous = message("old", {role: "user", client_message_id: "first"});
  assert.equal(state.mergeChatMessages([local], [previous]).length, 2);
  const current = message("new", {role: "user", client_message_id: "second"});
  assert.equal(state.mergeChatMessages([local], [previous, current]).length, 2);
});

test("Snapshots replace the prefix and preserve completed/partial states", () => {
  const completed = state.snapshotMessageUpdate({text: "Полный ответ", state: "completed"});
  assert.equal(completed.content, "Полный ответ"); assert.equal(completed.status, "completed");
  assert.equal(state.snapshotMessageUpdate({text: "Часть ответа", state: "failed"}).status, "partial");
  assert.equal(state.snapshotMessageUpdate({text: "", state: "cancelled"}).status, "failed");
});
