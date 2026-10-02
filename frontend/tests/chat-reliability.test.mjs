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

test("A terminal preflight SSE error rejects the send promise so the composer can restore the draft", async () => {
  const h = harness(() => stream(frame("error", {
    code: "validation_error",
    message: "Контекст запроса отклонён",
  })));
  await assert.rejects(
    h.run(),
    error => error?.status === 422 && /Контекст запроса отклонён/.test(error.message),
  );
  assert.equal(h.events.at(-1).event, "error");
  assert.equal(h.storage.size, 0);
  assert.equal(h.cancellations(), 0);
});

test("A terminal error after generation acceptance stays an authoritative server result", async () => {
  const h = harness(() => stream(
    frame("generation", {id: "generation"}) +
    frame("error", {code: "AI-102", message: "Провайдер недоступен"}),
  ));
  await h.run();
  assert.equal(h.events[0].event, "activity");
  assert.ok(h.events.some(event => event.event === "generation"));
  assert.equal(h.events.at(-1).event, "activity");
  assert.ok(h.events.some(event => event.event === "error"));
  assert.equal(h.storage.size, 0);
  assert.equal(h.cancellations(), 0);
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

const workspace = load("workspace-state");
test("A delayed conversation response cannot overwrite a newer navigation or New chat", () => {
  const requests = workspace.createLatestRequest();
  const old = requests.begin(); const current = requests.begin();
  assert.equal(requests.isCurrent(old), false);
  assert.equal(requests.isCurrent(current), true);
  requests.invalidate(); assert.equal(requests.isCurrent(current), false);
});

test("Clearing a submitted draft waits for an older autosave without blocking another chat", async () => {
  const enqueue = workspace.createDraftWriter(); const calls = [];
  let finish;
  const save = enqueue("chat-a", () => {calls.push("save"); return new Promise(resolve => {finish = resolve;});});
  const clear = enqueue("chat-a", async () => {calls.push("clear");});
  await enqueue("chat-b", async () => {calls.push("other");});
  assert.deepEqual(calls, ["save", "other"]);
  finish(); await Promise.all([save, clear]);
  assert.deepEqual(calls, ["save", "other", "clear"]);
});

test("A failed draft save does not poison the next save or delete", async () => {
  const enqueue = workspace.createDraftWriter();
  await assert.rejects(enqueue("chat", async () => {throw new Error("offline");}));
  assert.equal(await enqueue("chat", async () => "saved"), "saved");
});

test("Attachments wait for extraction, accept partial extraction and surface a failed file", () => {
  assert.equal(workspace.attachmentReadiness([{status:"parsing"}]).blocked, true);
  assert.equal(workspace.attachmentReadiness([{status:"ready"},{status:"partial"}]).blocked, false);
  assert.match(workspace.attachmentReadiness([{status:"failed"}]).message, /Уберите/);
  assert.equal(workspace.attachmentReadiness([]).blocked, false);
});

test("Attachment drafts belong to their conversation and survive reload as IDs only", () => {
 const saved=new Map();const storage={getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)};
 const selections=workspace.createAttachmentDrafts(storage);
 selections.update("a",()=>[{id:"file-a",original_name:"Private name"}]);
 selections.update("b",()=>[{id:"file-b"}]);
 selections.update("a",files=>[...files,{id:"late-upload"}]);
 assert.equal(JSON.stringify(selections.ids("a")),JSON.stringify(["file-a","late-upload"]));
 assert.equal(JSON.stringify(selections.ids("b")),JSON.stringify(["file-b"]));
 assert.ok([...saved.values()].every(value=>!value.includes("Private name")));
 const restored=workspace.createAttachmentDrafts(storage);
 assert.equal(JSON.stringify(restored.ids("a")),JSON.stringify(["file-a","late-upload"]));
 selections.update("a",files=>files.filter(file=>file.id!=="file-a"));
 assert.equal(JSON.stringify(workspace.createAttachmentDrafts(storage).ids("a")),JSON.stringify(["late-upload"]));
});

test("Attachment restoration rejects corrupt data and limits duplicate selections", () => {
 const selections=workspace.createAttachmentDrafts({getItem:key=>key.endsWith("bad")?"broken":JSON.stringify(["a","a",null,4,"","b","c","d","e"]),setItem:()=>{}});
 assert.equal(selections.ids("bad").length,0);
 assert.equal(JSON.stringify(selections.ids("valid")),JSON.stringify(["a","b","c","d"]));
});

const workflow = load("agent-workflow");
test("Visual workflow preserves explicit gaps and safely reorders DAG connections", () => {
 const nodes=[{id:"start",type:"llm",title:"Start",selected_model:"chosen"},{id:"b",type:"notify",title:"B"},{id:"c",type:"notify",title:"C"}];
 assert.equal(workflow.graphLinks({routing:"explicit",nodes,edges:[]}).length,0);
 const result=workflow.orderedGraph({nodes,edges:[{from:"start",to:"c"},{from:"c",to:"b"}]});
 assert.equal(JSON.stringify(result.nodes.map(node=>node.id)),JSON.stringify(["start","c","b"]));
 assert.equal(result.nodes[0].selected_model,"chosen");
 assert.throws(()=>workflow.orderedGraph({nodes,edges:[{from:"b",to:"c"},{from:"c",to:"b"}]}),/цикл/);
 assert.throws(()=>workflow.orderedGraph({nodes,edges:[{from:"b",to:"start"}]}),/Начальный/);
});
