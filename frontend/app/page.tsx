"use client";

import {FormEvent, KeyboardEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState} from "react";
import {ApiError, api, ensureCsrf, streamMessage} from "../lib/api";
import type {AIModel, ChatMessage, CompareRun, Conversation, FileAsset, GenerationMeta, ImageGeneration, ImageModel, MemoryCandidate, MemoryItem, Notification, Preference, Project, SearchResult, User, Wallet} from "../lib/types";
import {Icon} from "./components/icons";

type Panel = "search" | "projects" | "files" | "images" | "memory" | "wallet" | "notifications" | "settings" | "support" | "cost" | "compare" | null;

const money = (value: string | number | null | undefined) => `${Number(value ?? 0).toFixed(2).replace(".", ",")} ₽`;
const dateLabel = (value: string) => new Intl.DateTimeFormat("ru", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}).format(new Date(value));

function Button({children, className = "", ...props}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`button ${className}`} {...props}>{children}</button>;
}

function Drawer({title, onClose, children}: {title: string; onClose: () => void; children: ReactNode}) {
  useEffect(() => {
    const close = (event: globalThis.KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return <div className="drawerLayer" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawerTitle">
      <header><h2 id="drawerTitle">{title}</h2><button className="iconButton" onClick={onClose} aria-label="Закрыть"><Icon name="close" /></button></header>
      <div className="drawerBody">{children}</div>
    </section>
  </div>;
}

function AuthScreen({onAuthenticated}: {onAuthenticated: () => void}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    try {
      await ensureCsrf();
      await api(mode === "login" ? "/auth/login/" : "/auth/register/", {
        method: "POST",
        body: JSON.stringify({username: form.get("username"), email: form.get("email"), password: form.get("password")}),
      });
      onAuthenticated();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось войти"); }
    finally { setBusy(false); }
  };
  return <main className="authPage"><section className="authCard" aria-labelledby="authTitle">
    <div className="authBrand"><span><Icon name="spark" size={25}/></span> AI Workspace</div>
    <p className="eyebrow">ОДНО РАБОЧЕЕ ПРОСТРАНСТВО</p>
    <h1 id="authTitle">{mode === "login" ? "С возвращением" : "Начните работать с лучшими AI"}</h1>
    <p className="muted">Чаты, модели, проекты и файлы с единым рублёвым балансом.</p>
    <form onSubmit={submit} className="stack authForm">
      <label>Логин<input name="username" autoComplete="username" required /></label>
      {mode === "register" && <label>Email<input name="email" type="email" autoComplete="email" required /></label>}
      <label>Пароль<input name="password" type="password" minLength={8} autoComplete={mode === "login" ? "current-password" : "new-password"} required /></label>
      {error && <div className="alert error" role="alert">{error}</div>}
      <Button className="primary wide" disabled={busy}>{busy ? "Подождите…" : mode === "login" ? "Войти" : "Создать аккаунт"}</Button>
    </form>
    <button className="textButton" onClick={() => {setMode(mode === "login" ? "register" : "login"); setError("");}}>
      {mode === "login" ? "Нет аккаунта? Зарегистрироваться" : "Уже есть аккаунт? Войти"}
    </button>
  </section></main>;
}

export default function Home() {
  const [boot, setBoot] = useState<"loading" | "ready" | "guest" | "error">("loading");
  const [user, setUser] = useState<User | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [models, setModels] = useState<AIModel[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [files, setFiles] = useState<FileAsset[]>([]);
  const [imageModels, setImageModels] = useState<ImageModel[]>([]);
  const [imageGenerations, setImageGenerations] = useState<ImageGeneration[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [memoryCandidates, setMemoryCandidates] = useState<MemoryCandidate[]>([]);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [preferences, setPreferences] = useState<Preference | null>(null);
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);
  const [streamNote, setStreamNote] = useState("");
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel>(null);
  const [costMessage, setCostMessage] = useState<ChatMessage | null>(null);
  const [compareSource, setCompareSource] = useState<ChatMessage | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const active = conversations.find((item) => item.id === activeId) ?? null;
  const selectedModel = models.find((item) => item.slug === active?.selected_model) ?? models[0];
  const unread = notifications.filter((item) => !item.read_at).length;
  const lowBalance = wallet && preferences && Number(wallet.available_rub) <= Number(preferences.low_balance_threshold_rub);

  const loadWorkspace = useCallback(async () => {
    setBoot("loading"); setError("");
    try {
      await ensureCsrf();
      const me = await api<User>("/auth/me/");
      const [chatData, modelData, projectData, fileData, imageModelData, imageData, memoryData, candidateData, walletData, notificationData, preferenceData] = await Promise.all([
        api<Conversation[]>("/conversations/"), api<AIModel[]>("/models/"), api<Project[]>("/projects/"),
        api<FileAsset[]>("/files/"), api<ImageModel[]>("/image-models/"), api<ImageGeneration[]>("/images/generations/"), api<MemoryItem[]>("/memories/"), api<MemoryCandidate[]>("/memory-candidates/"), api<Wallet>("/wallet/"), api<Notification[]>("/auth/notifications/"), api<Preference>("/auth/preferences/"),
      ]);
      setUser(me); setConversations(chatData); setModels(modelData); setProjects(projectData);
      setFiles(fileData); setImageModels(imageModelData); setImageGenerations(imageData); setMemories(memoryData); setMemoryCandidates(candidateData); setWallet(walletData); setNotifications(notificationData); setPreferences(preferenceData);
      setActiveId((current) => current && chatData.some((item) => item.id === current) ? current : chatData[0]?.id ?? null);
      setBoot("ready");
    } catch (reason) {
      if (reason instanceof ApiError && [401, 403].includes(reason.status)) setBoot("guest");
      else { setError(reason instanceof Error ? reason.message : "Сервис временно недоступен"); setBoot("error"); }
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void loadWorkspace());
    navigator.serviceWorker?.register("/sw.js").catch(() => undefined);
  }, [loadWorkspace]);
  useEffect(() => {
    const shortcut = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setPanel("search"); }
      if (event.altKey && event.key.toLowerCase() === "n") { event.preventDefault(); document.querySelector<HTMLButtonElement>(".newChat")?.click(); }
    };
    window.addEventListener("keydown", shortcut); return () => window.removeEventListener("keydown", shortcut);
  });
  useEffect(() => { threadRef.current?.scrollTo({top: threadRef.current.scrollHeight, behavior: "smooth"}); }, [active?.messages, sending]);
  useEffect(() => {
    if (!focusMessageId || active?.id !== activeId) return;
    const timer = window.setTimeout(() => {
      document.getElementById(`message-${focusMessageId}`)?.scrollIntoView({behavior: "smooth", block: "center"});
    }, 80);
    const clear = window.setTimeout(() => setFocusMessageId(null), 2400);
    return () => {window.clearTimeout(timer); window.clearTimeout(clear);};
  }, [active?.id, activeId, focusMessageId]);
  useEffect(() => {
    if (!activeId || boot !== "ready") return;
    const localKey = `draft:${activeId}`;
    let cancelled = false;
    api<{content: string}>(`/conversations/${activeId}/draft/`).then((draft) => {
      if (!cancelled) setValue(draft.content || localStorage.getItem(localKey) || "");
    }).catch(() => { if (!cancelled) setValue(localStorage.getItem(localKey) || ""); });
    return () => { cancelled = true; };
  }, [activeId, boot]);
  useEffect(() => {
    if (!activeId || boot !== "ready") return;
    const key = `draft:${activeId}`; localStorage.setItem(key, value);
    const timer = window.setTimeout(() => {
      const request = value ? api(`/conversations/${activeId}/draft/`, {method: "PUT", body: JSON.stringify({content: value})})
        : api(`/conversations/${activeId}/draft/`, {method: "DELETE"});
      request.catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timer);
  }, [value, activeId, boot]);

  const replaceConversation = (conversation: Conversation) => setConversations((items) => [conversation, ...items.filter((item) => item.id !== conversation.id)]);
  const refreshConversation = async (id: string) => replaceConversation(await api<Conversation>(`/conversations/${id}/`));
  const refreshWallet = async () => setWallet(await api<Wallet>("/wallet/"));

  const createConversation = async () => {
    try {
      const payload: Record<string, string> = {title: "Новый чат", routing_mode: "balanced"};
      if (models.find((item) => item.available)) payload.selected_model = models.find((item) => item.available)!.slug;
      const conversation = await api<Conversation>("/conversations/", {method: "POST", body: JSON.stringify(payload)});
      replaceConversation(conversation); setActiveId(conversation.id); setValue(""); setSidebarOpen(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось создать чат"); }
  };

  const chooseConversation = (id: string, messageId?: string) => { setActiveId(id); setFocusMessageId(messageId ?? null); setSidebarOpen(false); setPanel(null); setError(""); };

  const send = async () => {
    const prompt = value.trim(); if (!prompt || sending) return;
    setError(""); setStreamNote("");
    let conversation = active;
    if (!conversation) {
      await createConversation();
      const latest = await api<Conversation[]>("/conversations/");
      conversation = latest[0]; setConversations(latest); setActiveId(conversation?.id ?? null);
    }
    if (!conversation) return;
    const userMessage: ChatMessage = {id: `local-user-${crypto.randomUUID()}`, branch: conversation.active_branch, role: "user", content: prompt, status: "saved", generation: null, created_at: new Date().toISOString()};
    const assistantId = `local-ai-${crypto.randomUUID()}`;
    const assistantMessage: ChatMessage = {id: assistantId, branch: conversation.active_branch, role: "assistant", content: "", status: "streaming", generation: null, created_at: new Date().toISOString()};
    replaceConversation({...conversation, messages: [...conversation.messages, userMessage, assistantMessage]});
    setValue(""); localStorage.removeItem(`draft:${conversation.id}`); setSending(true);
    const controller = new AbortController(); abortRef.current = controller;
    try {
      await streamMessage(conversation.id, {content: prompt, client_message_id: crypto.randomUUID()}, `web:${crypto.randomUUID()}`, ({event, data}) => {
        if (event === "delta") setConversations((items) => items.map((item) => item.id !== conversation!.id ? item : {...item, messages: item.messages.map((message) => message.id === assistantId ? {...message, content: message.content + String(data.text ?? "")} : message)}));
        if (event === "recovery") setStreamNote(data.action === "fallback" ? "Подключаем резервную модель…" : "Провайдер не ответил, повторяем безопасно…");
        if (event === "routing") setStreamNote(String(data.explanation ?? "AUTO выбрал подходящую модель"));
        if (event === "memory") setStreamNote(String(data.message ?? "Память обновлена"));
        if (event === "memory_candidates") {setStreamNote(String(data.message ?? "Найдены предложения для памяти")); void api<MemoryCandidate[]>("/memory-candidates/").then(setMemoryCandidates).catch(() => undefined);}
        if (event === "error") setError(String(data.message ?? "Ответ не получен. Деньги не списаны."));
      }, controller.signal);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "Соединение прервано");
    } finally {
      setSending(false); setStreamNote(""); abortRef.current = null;
      await Promise.all([refreshConversation(conversation.id), refreshWallet()]).catch(() => undefined);
    }
  };

  const composerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
  };

  const updateConversation = async (changes: Partial<Pick<Conversation, "selected_model" | "routing_mode" | "project" | "title" | "memory_enabled">>) => {
    if (!active) return;
    try { replaceConversation(await api<Conversation>(`/conversations/${active.id}/`, {method: "PATCH", body: JSON.stringify(changes)})); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось изменить чат"); }
  };
  const activateBranch = async (branchId: string) => {
    if (!active) return;
    try {replaceConversation(await api<Conversation>(`/conversations/${active.id}/branches/${branchId}/activate/`, {method: "POST", body: "{}"}));}
    catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось открыть ветку");}
  };

  const estimatedCost = useMemo(() => {
    if (active?.routing_mode !== "manual") return null;
    if (!selectedModel?.price) return null;
    const inputTokens = Math.max(value.length, 32);
    const provider = (inputTokens * Number(selectedModel.price.input_rub_per_million) + Math.min(1024, selectedModel.max_output_tokens) * Number(selectedModel.price.output_rub_per_million)) / 1_000_000;
    return provider * (1 + Number(selectedModel.price.markup_percent) / 100);
  }, [active?.routing_mode, selectedModel, value]);

  if (boot === "loading") return <main className="centerState" aria-busy="true"><div className="loader"/><h1>Открываем рабочее пространство</h1><p>Загружаем чаты, проекты и баланс…</p></main>;
  if (boot === "guest") return <AuthScreen onAuthenticated={loadWorkspace}/>;
  if (boot === "error") return <main className="centerState"><div className="stateIcon">!</div><h1>Не удалось подключиться</h1><p>{error}</p><Button className="primary" onClick={loadWorkspace}>Повторить</Button></main>;

  return <main className="appShell">
    <a className="skipLink" href="#composer">Перейти к сообщению</a>
    <div className={`mobileShade ${sidebarOpen ? "visible" : ""}`} onClick={() => setSidebarOpen(false)}/>
    <aside className={`sidebar ${sidebarOpen ? "open" : ""}`} aria-label="Основная навигация">
      <div className="brand"><span><Icon name="spark" size={20}/></span><b>AI Workspace</b><button className="mobileClose" onClick={() => setSidebarOpen(false)} aria-label="Закрыть меню"><Icon name="close"/></button></div>
      <Button className="newChat" onClick={createConversation}><Icon name="plus"/> Новый чат <kbd>Alt N</kbd></Button>
      <button className="searchButton" onClick={() => setPanel("search")}><Icon name="search"/><span>Поиск</span><kbd>⌘ K</kbd></button>
      <nav className="mainNav" aria-label="Разделы">
        <button onClick={() => setPanel("projects")}><Icon name="folder"/>Проекты<span>{projects.filter((item) => !item.archived_at).length}</span></button>
        <button onClick={() => setPanel("files")}><Icon name="file"/>Файлы<span>{files.length}</span></button>
        <button onClick={() => setPanel("images")}><Icon name="image"/>Изображения<span>{imageGenerations.reduce((sum, item) => sum + item.actual_count, 0)}</span></button>
        <button onClick={() => setPanel("memory")}><Icon name="memory"/>Память<span>{memoryCandidates.length > 0 ? `+${memoryCandidates.length}` : memories.filter((item) => item.status === "active").length}</span></button>
      </nav>
      <div className="historyLabel">Недавние чаты</div>
      <div className="history">
        {conversations.length === 0 && <p className="sidebarEmpty">История появится после первого сообщения</p>}
        {conversations.map((conversation) => <button key={conversation.id} className={conversation.id === activeId ? "active" : ""} onClick={() => chooseConversation(conversation.id)}><span>{conversation.title}</span><small>{dateLabel(conversation.updated_at)}</small></button>)}
      </div>
      <div className="sidebarBottom">
        <button className={`balanceCard ${lowBalance ? "low" : ""}`} onClick={() => setPanel("wallet")}><span><Icon name="wallet"/><small>Баланс</small></span><strong>{money(wallet?.available_rub)}</strong></button>
        <div className="profileRow"><div className="avatar">{user?.username.slice(0, 1).toUpperCase()}</div><button onClick={() => setPanel("settings")}><b>{user?.username}</b><small>Настройки аккаунта</small></button><button className="iconButton notificationButton" onClick={() => setPanel("notifications")} aria-label={`Уведомления: ${unread}`}><Icon name="bell"/>{unread > 0 && <i>{unread}</i>}</button></div>
      </div>
    </aside>

    <section className="workspace">
      <header className="topbar">
        <button className="menuButton" onClick={() => setSidebarOpen(true)} aria-label="Открыть меню"><Icon name="menu"/></button>
        <div className="selectors">
          <label className="selectWrap"><span className="srOnly">Режим и модель</span><select value={active ? active.routing_mode === "manual" ? `model:${active.selected_model}` : `auto:${active.routing_mode}` : ""} disabled={!active || sending} onChange={(event) => {const [kind, value] = event.target.value.split(":"); void (kind === "auto" ? updateConversation({routing_mode: value as Conversation["routing_mode"]}) : updateConversation({routing_mode: "manual", selected_model: value}));}}>
            <optgroup label="AUTO Router"><option value="auto:balanced">AUTO · Баланс</option><option value="auto:economy">AUTO · Эконом</option><option value="auto:maximum">AUTO · Максимум</option></optgroup>
            <optgroup label="Выбрать вручную">{models.length === 0 && <option value="">Модели не настроены</option>}{models.map((model) => <option key={model.slug} value={`model:${model.slug}`} disabled={!model.available}>{model.display_name}{model.model_version ? ` · ${model.model_version}` : ""}{!model.available ? " · недоступна" : ""}</option>)}</optgroup>
          </select></label>
          <label className="selectWrap projectSelect"><span className="srOnly">Проект</span><select value={active?.project ?? ""} disabled={!active || sending} onChange={(event) => updateConversation({project: event.target.value || null})}>
            <option value="">Без проекта</option>{projects.filter((project) => !project.archived_at && project.role !== "viewer").map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select></label>
          {active && active.branches.length > 1 && <label className="selectWrap branchSelect"><span className="srOnly">Ветка</span><select value={active.active_branch ?? ""} onChange={(event) => activateBranch(event.target.value)}>{active.branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.title}</option>)}</select></label>}
        </div>
        <div className="systemStatus"><span/> Системы работают</div>
        {active && <button className={`memoryToggle ${active.memory_enabled ? "active" : ""}`} onClick={() => updateConversation({memory_enabled: !active.memory_enabled})} title={active.memory_enabled ? "Отключить память для чата" : "Включить память для чата"}><Icon name="memory" size={17}/><span>{active.memory_enabled ? "Память" : "Без памяти"}</span></button>}
        <button className="topBalance" onClick={() => setPanel("wallet")}><Icon name="wallet" size={18}/>{money(wallet?.available_rub)}</button>
      </header>

      <div className="thread" ref={threadRef} aria-live="polite">
        {!active || active.messages.length === 0 ? <section className="emptyChat"><div className="spark"><Icon name="spark" size={30}/></div><p className="eyebrow">AI WORKSPACE</p><h1>Чем займёмся?</h1><p>Выберите задачу или начните с собственного вопроса.</p><div className="suggestions">
          {["Составь маркетинговую стратегию", "Проанализируй документ", "Помоги написать код", "Сравни варианты решения"].map((text) => <button key={text} onClick={() => setValue(text)}>{text}<span>↗</span></button>)}
        </div></section> : <div className="messageList">
          {active.messages.map((message, messageIndex) => <article id={`message-${message.id}`} key={message.id} className={`message ${message.role} ${message.status} ${focusMessageId === message.id ? "searchFocus" : ""}`}>
            <div className="messageAvatar">{message.role === "user" ? user?.username.slice(0, 1).toUpperCase() : <Icon name="spark" size={17}/>}</div>
            <div className="messageBody"><div className="messageHead"><b>{message.role === "user" ? "Вы" : "AI Workspace"}</b><time>{dateLabel(message.created_at)}</time>{message.status === "partial" && <span className="statusPill warning">Ответ прервался</span>}{message.status === "failed" && <span className="statusPill danger">Ошибка</span>}</div>
              <div className="messageText">{message.content || (message.status === "streaming" ? <span className="typing"><i/><i/><i/></span> : "Ответ не получен")}</div>
              {message.role === "assistant" && message.content && <div className="messageActions"><button onClick={() => navigator.clipboard.writeText(message.content)}><Icon name="copy" size={16}/>Копировать</button><button onClick={() => {const source = active.messages.slice(0, messageIndex).reverse().find((item) => item.role === "user"); if (source) {setCompareSource(source); setPanel("compare");}}}>Сравнить</button>{message.generation && <button onClick={() => {setCostMessage(message); setPanel("cost");}}>{money(message.generation.cost_rub)} · {message.generation.model}</button>}</div>}
            </div>
          </article>)}
        </div>}
      </div>

      <div className="composerZone" id="composer">
        {error && <div className="inlineError" role="alert"><span>!</span><p>{error}</p><button onClick={() => setError("")} aria-label="Закрыть ошибку"><Icon name="close" size={17}/></button></div>}
        {streamNote && <div className="recoveryNote"><div className="miniLoader"/>{streamNote}</div>}
        {lowBalance && <button className="lowBalanceNotice" onClick={() => setPanel("wallet")}>Баланс заканчивается. Пополнить →</button>}
        <div className={`composer ${sending ? "busy" : ""}`}>
          <textarea aria-label="Сообщение" value={value} onChange={(event) => setValue(event.target.value)} onKeyDown={composerKey} placeholder="Напишите сообщение…" rows={1}/>
          <div className="composerActions"><button className="attach" onClick={() => setPanel("files")} aria-label="Прикрепить файл"><Icon name="plus"/></button><span className="costPreview">{active?.routing_mode !== "manual" ? "AUTO рассчитает цену" : estimatedCost === null ? "Цена по факту" : `до ${money(Math.max(estimatedCost, 0.01))}`}</span>{sending ? <button className="send stop" onClick={() => abortRef.current?.abort()} aria-label="Остановить генерацию"><Icon name="stop"/></button> : <button className="send" onClick={send} disabled={!value.trim()} aria-label="Отправить"><Icon name="send"/></button>}</div>
        </div>
        <p className="composerHint">Enter - отправить · Shift+Enter - новая строка · ошибки провайдера не оплачиваются</p>
      </div>
    </section>

    {panel === "search" && <SearchPanel projects={projects} onChoose={chooseConversation} onClose={() => setPanel(null)}/>} 
    {panel === "projects" && <ProjectsPanel projects={projects} onChange={setProjects} onClose={() => setPanel(null)}/>} 
    {panel === "files" && <FilesPanel files={files} projects={projects} onChange={setFiles} onClose={() => setPanel(null)}/>} 
    {panel === "images" && <ImageStudioPanel models={imageModels} generations={imageGenerations} onChange={setImageGenerations} onWalletChange={refreshWallet} onClose={() => setPanel(null)}/>} 
    {panel === "memory" && <MemoryPanel items={memories} candidates={memoryCandidates} projects={projects} conversations={conversations} onChange={setMemories} onCandidatesChange={setMemoryCandidates} onClose={() => setPanel(null)}/>} 
    {panel === "wallet" && <WalletPanel wallet={wallet} onClose={() => setPanel(null)}/>} 
    {panel === "notifications" && <NotificationsPanel items={notifications} onChange={setNotifications} onClose={() => setPanel(null)}/>} 
    {panel === "settings" && preferences && user && <SettingsPanel user={user} preferences={preferences} onChange={(value) => {setPreferences(value); if (!value.auto_memory_enabled) setMemoryCandidates([]);}} onLogout={() => {setUser(null); setBoot("guest");}} onClose={() => setPanel(null)}/>} 
    {panel === "support" && <SupportPanel onClose={() => setPanel(null)}/>} 
    {panel === "cost" && costMessage?.generation && <Drawer title="Стоимость и контекст" onClose={() => setPanel(null)}><div className="costHero"><small>Списано по факту</small><strong>{money(costMessage.generation.cost_rub)}</strong></div><dl className="details"><div><dt>Модель</dt><dd>{costMessage.generation.model}</dd></div><div><dt>Провайдер</dt><dd>{costMessage.generation.provider || "—"}</dd></div><div><dt>Входные токены</dt><dd>{costMessage.generation.input_tokens}</dd></div><div><dt>Выходные токены</dt><dd>{costMessage.generation.output_tokens}</dd></div><div><dt>Correlation ID</dt><dd className="mono">{costMessage.generation.correlation_id}</dd></div></dl><ContextInspector context={costMessage.generation.context}/></Drawer>}
    {panel === "compare" && active && compareSource && <ComparePanel conversation={active} source={compareSource} models={models} onConversation={replaceConversation} onClose={() => setPanel(null)}/>} 
    <button className="supportFloat" onClick={() => setPanel("support")} aria-label="Написать в поддержку"><Icon name="support"/></button>
  </main>;
}

function ComparePanel({conversation, source, models, onConversation, onClose}: {conversation: Conversation; source: ChatMessage; models: AIModel[]; onConversation: (value: Conversation) => void; onClose: () => void}) {
  const eligible = models.filter((model) => model.available && model.capabilities.includes("text"));
  const [selected, setSelected] = useState<string[]>(eligible.slice(0, 2).map((model) => model.slug));
  const [preview, setPreview] = useState<{expected_min_rub: string; expected_max_rub: string; confirmation_required: boolean} | null>(null);
  const [result, setResult] = useState<CompareRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (selected.length < 2) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const data = await api<{expected_min_rub: string; expected_max_rub: string; confirmation_required: boolean}>(`/conversations/${conversation.id}/compare/preview/`, {method: "POST", body: JSON.stringify({prompt: source.content, source_message: source.id, models: selected})});
        if (!cancelled) {setPreview(data); setError("");}
      } catch (reason) {if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось рассчитать стоимость");}
    }, 250);
    return () => {cancelled = true; window.clearTimeout(timer);};
  }, [conversation.id, source.content, source.id, selected]);
  const toggle = (slug: string) => setSelected((items) => items.includes(slug) ? items.filter((item) => item !== slug) : items.length < 4 ? [...items, slug] : items);
  const run = async () => {
    if (selected.length < 2) return;
    setBusy(true); setError("");
    try {setResult(await api<CompareRun>(`/conversations/${conversation.id}/compare/`, {method: "POST", headers: {"Idempotency-Key": `web-compare:${crypto.randomUUID()}`}, body: JSON.stringify({prompt: source.content, source_message: source.id, models: selected, confirm_cost: true})}));}
    catch (reason) {setError(reason instanceof Error ? reason.message : "Сравнение не выполнено");}
    finally {setBusy(false);}
  };
  const synthesize = async () => {
    if (!result) return;
    setBusy(true); setError("");
    try {setResult(await api<CompareRun>(`/conversations/${conversation.id}/compare/${result.id}/synthesize/`, {method: "POST", body: JSON.stringify({model: selected[0], confirm_cost: true})}));}
    catch (reason) {setError(reason instanceof Error ? reason.message : "Синтез не выполнен");}
    finally {setBusy(false);}
  };
  const branch = async (variantId: string) => {
    setBusy(true); setError("");
    try {onConversation(await api<Conversation>(`/conversations/${conversation.id}/compare/${result!.id}/variants/${variantId}/branch/`, {method: "POST", body: "{}"})); onClose();}
    catch (reason) {setError(reason instanceof Error ? reason.message : "Ветка не создана"); setBusy(false);}
  };
  return <Drawer title="Сравнить модели" onClose={onClose}><div className="comparePrompt"><small>Один запрос для всех моделей</small><p>{source.content}</p></div>{!result && <><div className="compareModels">{eligible.map((model) => <label key={model.slug} className={selected.includes(model.slug) ? "selected" : ""}><input type="checkbox" checked={selected.includes(model.slug)} onChange={() => toggle(model.slug)}/><span><b>{model.display_name}</b><small>{model.provider}</small></span></label>)}</div>{preview && selected.length >= 2 && <div className="compareCost"><span>Ожидаемая стоимость</span><b>{money(preview.expected_min_rub)} - {money(preview.expected_max_rub)}</b><small>Верхний лимит резервируется до запуска, списание - по факту.</small></div>}<Button className="primary wide" disabled={busy || selected.length < 2} onClick={run}>{busy ? "Модели отвечают параллельно…" : `Запустить ${selected.length} модели`}</Button></>}{error && <div className="alert error">{error}</div>}{result && <><div className="compareSummary"><span>{result.state === "completed" ? "Сравнение готово" : "Готово частично"}</span><b>Списано {money(result.actual_cost_rub)}</b></div><div className="compareResults">{result.variants.map((variant) => <article key={variant.id} className={variant.state}><header><div><b>{variant.model_name}</b><small>{variant.provider} · {money(variant.actual_cost_rub)}</small></div>{variant.state === "completed" && <button disabled={busy} onClick={() => branch(variant.id)}>Продолжить веткой</button>}</header><p>{variant.output || "Ответ не получен"}</p></article>)}</div>{result.synthesis_output ? <article className="synthesisResult"><header><b>Итоговый ответ</b><small>{money(result.synthesis_cost_rub)}</small></header><p>{result.synthesis_output}</p></article> : <Button className="primary wide" disabled={busy || result.variants.filter((item) => item.state === "completed").length < 2} onClick={synthesize}>{busy ? "Формируем итог…" : "Сформировать лучший итог"}</Button>}</>}</Drawer>;
}

function ImageStudioPanel({models, generations, onChange, onWalletChange, onClose}: {models: ImageModel[]; generations: ImageGeneration[]; onChange: (value: ImageGeneration[]) => void; onWalletChange: () => Promise<void>; onClose: () => void}) {
  const [modelSlug, setModelSlug] = useState(models[0]?.slug ?? "");
  const model = models.find((item) => item.slug === modelSlug) ?? models[0];
  const [prompt, setPrompt] = useState("");
  const [size, setSize] = useState(model?.supported_sizes[0] ?? "");
  const [quality, setQuality] = useState(model?.supported_qualities[0] ?? "");
  const [count, setCount] = useState(1);
  const [preview, setPreview] = useState<{expected_cost_rub: string; confirmation_required: boolean} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!model || !prompt.trim() || !size || !quality) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const value = await api<{expected_cost_rub: string; confirmation_required: boolean}>("/images/preview/", {method: "POST", body: JSON.stringify({model: model.slug, prompt, size, quality, count})});
        if (!cancelled) {setPreview(value); setError("");}
      } catch (reason) {if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось рассчитать стоимость");}
    }, 300);
    return () => {cancelled = true; window.clearTimeout(timer);};
  }, [model, prompt, size, quality, count]);
  const run = async () => {
    if (!model || !prompt.trim()) return;
    setBusy(true); setError("");
    try {
      const generated = await api<ImageGeneration>("/images/generations/", {method: "POST", headers: {"Idempotency-Key": `web-image:${crypto.randomUUID()}`}, body: JSON.stringify({model: model.slug, prompt, size, quality, count, confirm_cost: true})});
      onChange([generated, ...generations.filter((item) => item.id !== generated.id)]); setPrompt(""); setPreview(null); await onWalletChange();
    } catch (reason) {setError(reason instanceof Error ? reason.message : "Изображение не создано");}
    finally {setBusy(false);}
  };
  return <Drawer title="Изображения" onClose={onClose}><section className="imageStudio">
    {models.length === 0 ? <div className="emptyPanel"><Icon name="image" size={32}/><h3>Image-модели не настроены</h3><p>Администратор может подключить провайдера и цену в Django admin.</p></div> : <>
      <div className="imageComposer"><label>Описание<textarea value={prompt} onChange={(event) => {setPrompt(event.target.value); if (!event.target.value.trim()) setPreview(null);}} maxLength={4000} rows={4} placeholder="Опишите сцену, стиль, свет и композицию…"/></label><div className="imageOptions"><label>Модель<select value={modelSlug} onChange={(event) => {const next = models.find((item) => item.slug === event.target.value); setModelSlug(event.target.value); setSize(next?.supported_sizes[0] ?? ""); setQuality(next?.supported_qualities[0] ?? ""); setCount((value) => Math.min(value, next?.max_images ?? 1)); setPreview(null);}}>{models.map((item) => <option key={item.slug} value={item.slug}>{item.display_name}</option>)}</select></label><label>Размер<select value={size} onChange={(event) => setSize(event.target.value)}>{model?.supported_sizes.map((item) => <option key={item}>{item}</option>)}</select></label><label>Качество<select value={quality} onChange={(event) => setQuality(event.target.value)}>{model?.supported_qualities.map((item) => <option key={item}>{item}</option>)}</select></label><label>Количество<select value={count} onChange={(event) => setCount(Number(event.target.value))}>{Array.from({length: Math.min(model?.max_images ?? 1, 4)}, (_, index) => index + 1).map((item) => <option key={item}>{item}</option>)}</select></label></div>{preview && <div className="imageCost"><span>Ожидаемая стоимость</span><b>{money(preview.expected_cost_rub)}</b><small>{preview.confirmation_required ? "Стоимость будет явно подтверждена при запуске." : "Резерв до запуска, списание только за сохранённые результаты."}</small></div>}{error && <div className="alert error">{error}</div>}<Button className="primary wide" disabled={busy || !prompt.trim() || !preview} onClick={run}>{busy ? "Создаём изображения…" : `Создать ${count}`}</Button></div>
      <header className="galleryHeader"><div><h3>Галерея</h3><p>История генераций и оригиналы с защищёнными source links.</p></div><span>{generations.reduce((sum, item) => sum + item.actual_count, 0)}</span></header>
      {generations.length === 0 ? <p className="muted">Здесь появятся созданные изображения.</p> : <div className="imageHistory">{generations.map((generation) => <article key={generation.id}><header><div><b>{generation.model_name}</b><small>{dateLabel(generation.created_at)} · {generation.size} · {generation.quality}</small></div><span className={`statusPill ${generation.state === "failed" ? "danger" : ""}`}>{generation.state === "completed" ? money(generation.actual_cost_rub) : generation.state === "running" ? "В работе" : "Ошибка"}</span></header><p>{generation.prompt}</p>{generation.images.length > 0 && <div className="imageGrid">{generation.images.map((image) => <figure key={image.id}><img src={image.source_url} alt={image.revised_prompt || generation.prompt}/><figcaption><span>#{image.position + 1} · {(image.size_bytes / 1024).toFixed(0)} КБ</span><a href={image.source_url} target="_blank" rel="noreferrer">Оригинал ↗</a></figcaption></figure>)}</div>}{generation.error_code && <small className="imageError">Код ошибки: {generation.error_code}. Средства не списаны.</small>}</article>)}</div>}
    </>}
  </section></Drawer>;
}

function SearchPanel({projects, onClose, onChoose}: {projects: Project[]; onClose: () => void; onChoose: (id: string, messageId?: string) => void}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [scope, setScope] = useState("all");
  const [project, setProject] = useState("");
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (query.trim().length < 2) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setBusy(true); setError("");
      const params = new URLSearchParams({q: query.trim()});
      if (scope !== "all") params.set("type", scope);
      if (project) params.set("project", project);
      if (role) {params.set("role", role); params.set("type", "message");}
      try {
        const data = await api<{results: SearchResult[]}>(`/search/?${params}`);
        if (!cancelled) setResults(data.results);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Ошибка поиска");
      } finally {
        if (!cancelled) setBusy(false);
      }
    }, 300);
    return () => {cancelled = true; clearTimeout(timer);};
  }, [query, scope, project, role]);
  const typeLabel = (item: SearchResult) => item.type === "message" ? (item.match === "semantic" ? "По смыслу" : item.match === "hybrid" ? "Точное + смысл" : "Совпадение") : item.type === "conversation" ? "Чат" : item.type === "project" ? "Проект" : "Файл";
  return <Drawer title="Поиск по рабочему пространству" onClose={onClose}><div className="searchField"><Icon name="search"/><input autoFocus value={query} onChange={(event) => {setQuery(event.target.value); if (event.target.value.trim().length < 2) setResults([]);}} placeholder="Найдите решение, факт или обсуждение" aria-label="Поисковый запрос"/>{busy && <div className="miniLoader"/>}</div><div className="searchFilters"><select value={scope} onChange={(event) => {setScope(event.target.value); if (event.target.value !== "message") setRole("");}} aria-label="Тип результата"><option value="all">Везде</option><option value="message">Сообщения</option><option value="conversation">Чаты</option><option value="project">Проекты</option><option value="file">Файлы</option></select><select value={project} onChange={(event) => setProject(event.target.value)} aria-label="Проект"><option value="">Все проекты</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><select value={role} onChange={(event) => {setRole(event.target.value); if (event.target.value) setScope("message");}} aria-label="Автор сообщения"><option value="">Все авторы</option><option value="user">Я</option><option value="assistant">AI</option></select></div>{error && <div className="alert error">{error}</div>}<div className="resultList">{query.length < 2 && <Empty icon="search" title="Введите минимум два символа" text="Поиск работает только в доступных вам данных."/>}{query.length >= 2 && !busy && results.length === 0 && <Empty icon="search" title="Ничего не найдено" text="Попробуйте другую формулировку или снимите фильтры."/>}{query.length >= 2 && results.map((item) => <button key={`${item.type}:${item.id}`} onClick={() => {if (item.type === "conversation" || item.type === "message") onChoose(item.conversation_id ?? item.id, item.navigation.message_id); else onClose();}}><span className="resultIcon"><Icon name={item.type === "project" ? "folder" : item.type === "file" ? "file" : "chat"}/></span><span><b>{item.title}</b><small>{item.excerpt}</small></span><em>{typeLabel(item)}</em></button>)}</div></Drawer>;
}

function ProjectsPanel({projects, onChange, onClose}: {projects: Project[]; onChange: (items: Project[]) => void; onClose: () => void}) {
  const [creating, setCreating] = useState(false); const [error, setError] = useState("");
  const create = async (event: FormEvent<HTMLFormElement>) => {event.preventDefault(); const data = new FormData(event.currentTarget); try {const project = await api<Project>("/projects/", {method: "POST", body: JSON.stringify({name: data.get("name"), description: data.get("description"), instruction: data.get("instruction")})}); onChange([project, ...projects]); setCreating(false);} catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось создать проект");}};
  return <Drawer title="Проекты" onClose={onClose}>{creating ? <form className="stack" onSubmit={create}><label>Название<input name="name" required autoFocus/></label><label>Описание<textarea name="description" rows={3}/></label><label>Инструкция для AI<textarea name="instruction" rows={5} placeholder="Контекст, правила и цель проекта"/></label>{error && <div className="alert error">{error}</div>}<div className="row"><Button type="button" onClick={() => setCreating(false)}>Отмена</Button><Button className="primary">Создать</Button></div></form> : <><Button className="primary wide" onClick={() => setCreating(true)}><Icon name="plus"/>Создать проект</Button><div className="cardList">{projects.length === 0 && <Empty icon="folder" title="Проектов пока нет" text="Объедините чаты, инструкции и файлы одной задачей."/>}{projects.map((project) => <article key={project.id} className={project.archived_at ? "dim" : ""}><span className="cardIcon"><Icon name="folder"/></span><div><h3>{project.name}</h3><p>{project.description || "Без описания"}</p><small>{project.role} · {project.archived_at ? "в архиве" : "активен"}</small></div></article>)}</div></>}</Drawer>;
}

function FilesPanel({files, projects, onChange, onClose}: {files: FileAsset[]; projects: Project[]; onChange: (items: FileAsset[]) => void; onClose: () => void}) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const fileRef = useRef<HTMLInputElement>(null);
  const upload = async (event: FormEvent<HTMLFormElement>) => {event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); setBusy(true); setError(""); try {const item = await api<FileAsset>("/files/", {method: "POST", headers: {"Idempotency-Key": `web-file:${crypto.randomUUID()}`}, body: data}); onChange([item, ...files]); form.reset();} catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось загрузить файл");} finally {setBusy(false);}};
  return <Drawer title="Файлы" onClose={onClose}><form className="uploadBox" onSubmit={upload}><input ref={fileRef} name="file" type="file" accept=".txt,.md,.csv,.docx,.xlsx,.pdf,.png,.jpg,.jpeg,.webp" required/><label>Проект<select name="project" required defaultValue=""><option value="" disabled>Выберите проект</option>{projects.filter((project) => !project.archived_at && project.role !== "viewer").map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><Button type="button" onClick={() => fileRef.current?.click()}><Icon name="plus"/>Выбрать файл</Button><Button className="primary" disabled={busy || projects.length === 0}>{busy ? "Проверяем…" : "Загрузить"}</Button><small>До 20 МБ · TXT, MD, CSV, DOCX, XLSX, PDF и изображения</small></form>{error && <div className="alert error">{error}</div>}<div className="cardList compact">{files.length === 0 && <Empty icon="file" title="Файлов пока нет" text="Загрузите документ в проект и следите за статусом обработки."/>}{files.map((file) => <article key={file.id}><span className="cardIcon"><Icon name="file"/></span><div><h3>{file.original_name}</h3><p>{(file.size_bytes / 1024).toFixed(1)} КБ · {file.detected_type.toUpperCase()}</p><small className={`fileStatus ${file.status}`}>{file.status === "ready" ? "Готов" : file.status === "partial" ? "Частично обработан" : file.status === "failed" ? "Ошибка" : "Обрабатывается"}</small></div></article>)}</div></Drawer>;
}

function MemoryPanel({items, candidates, projects, conversations, onChange, onCandidatesChange, onClose}: {items: MemoryItem[]; candidates: MemoryCandidate[]; projects: Project[]; conversations: Conversation[]; onChange: (items: MemoryItem[]) => void; onCandidatesChange: (items: MemoryCandidate[]) => void; onClose: () => void}) {
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [scope, setScope] = useState<MemoryItem["scope"]>("global");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const visible = items.filter((item) => item.status !== "deleted" && item.content.toLowerCase().includes(query.toLowerCase()));
  const beginCreate = () => {setEditing(null); setScope("global"); setCreating(true); setError("");};
  const beginEdit = (item: MemoryItem) => {setEditing(item); setScope(item.scope); setCreating(true); setError("");};
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const payload = {content: data.get("content"), scope, memory_type: data.get("memory_type"), project: scope === "project" ? data.get("project") : null, conversation: scope === "conversation" ? data.get("conversation") : null, importance_score: data.get("importance_score"), enabled: data.get("enabled") === "on"};
    try {
      const saved = await api<MemoryItem>(editing ? `/memories/${editing.id}/` : "/memories/", {method: editing ? "PATCH" : "POST", body: JSON.stringify(payload)});
      onChange([saved, ...items.filter((item) => item.id !== saved.id)]); setCreating(false); setEditing(null);
    } catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось сохранить память");}
  };
  const action = async (item: MemoryItem, name: "archive" | "pin" | "delete") => {
    try {
      if (name === "delete") {await api(`/memories/${item.id}/`, {method: "DELETE"}); onChange(items.filter((current) => current.id !== item.id)); return;}
      const updated = await api<MemoryItem>(`/memories/${item.id}/${name}/`, {method: "POST"});
      onChange(items.map((current) => current.id === item.id ? updated : current));
    } catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось изменить память");}
  };
  const review = async (candidate: MemoryCandidate, decision: "accept" | "reject") => {
    try {
      if (decision === "accept") {
        const result = await api<{candidate: MemoryCandidate; memory: MemoryItem}>(`/memory-candidates/${candidate.id}/accept/`, {method: "POST", body: JSON.stringify({scope: candidate.suggested_scope})});
        onChange([result.memory, ...items.filter((item) => item.id !== result.memory.id)]);
      } else await api(`/memory-candidates/${candidate.id}/reject/`, {method: "POST"});
      onCandidatesChange(candidates.filter((item) => item.id !== candidate.id));
    } catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось обработать предложение");}
  };
  const dismissAll = async () => {try {await api("/memory-candidates/dismiss-all/", {method: "POST"}); onCandidatesChange([]);} catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось скрыть предложения");}};
  if (creating) return <Drawer title="Память" onClose={onClose}><form className="stack memoryForm" onSubmit={save}><p className="muted">Активная память сохраняется только по вашему действию. Ответы AI и содержимое файлов автоматически сюда не попадают.</p><label>Что запомнить<textarea name="content" rows={5} required autoFocus defaultValue={editing?.content}/></label><div className="twoColumns"><label>Тип<select name="memory_type" defaultValue={editing?.memory_type ?? "fact"}><option value="fact">Факт</option><option value="preference">Предпочтение</option><option value="instruction">Инструкция</option><option value="decision">Решение</option></select></label><label>Область<select value={scope} onChange={(event) => setScope(event.target.value as MemoryItem["scope"])}><option value="global">Все чаты</option><option value="project">Проект</option><option value="conversation">Только чат</option></select></label></div>{scope === "project" && <label>Проект<select name="project" required defaultValue={editing?.project ?? ""}><option value="" disabled>Выберите проект</option>{projects.filter((project) => !project.archived_at && project.role !== "viewer").map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>}{scope === "conversation" && <label>Чат<select name="conversation" required defaultValue={editing?.conversation ?? ""}><option value="" disabled>Выберите чат</option>{conversations.map((conversation) => <option key={conversation.id} value={conversation.id}>{conversation.title}</option>)}</select></label>}<label>Важность<input name="importance_score" type="range" min="0" max="1" step="0.1" defaultValue={editing?.importance_score ?? "0.5"}/></label><label className="check"><input name="enabled" type="checkbox" defaultChecked={editing?.enabled ?? true}/>Использовать в контексте</label>{error && <div className="alert error">{error}</div>}<div className="row"><Button type="button" onClick={() => setCreating(false)}>Отмена</Button><Button className="primary">Сохранить</Button></div></form></Drawer>;
  return <Drawer title="Память" onClose={onClose}><div className="memoryIntro"><Icon name="memory" size={23}/><div><b>Память принадлежит вам</b><p>Автоматически найденные факты сначала попадают в предложения и не используются без подтверждения.</p></div></div>{error && <div className="alert error">{error}</div>}{candidates.length > 0 && <section className="candidateSection"><header><div><h3>Предложения <span>{candidates.length}</span></h3><p>Проверьте найденные факты перед сохранением.</p></div><button onClick={dismissAll}>Скрыть все</button></header><div className="candidateList">{candidates.map((candidate) => <article key={candidate.id} className={candidate.status}><div className="candidateMeta"><span>{candidate.status === "conflict" ? "Есть конфликт" : "Предложение"}</span><small>Уверенность {Math.round(Number(candidate.confidence_score) * 100)}% · прямое сообщение</small></div><p>{candidate.content}</p>{candidate.conflict_content && <div className="conflictNote"><small>Сейчас сохранено</small>{candidate.conflict_content}</div>}<div className="candidateScope">Сохранить: {candidate.suggested_scope === "global" ? "во всех чатах" : candidate.suggested_scope === "project" ? "в проекте" : "только в этом чате"}</div><footer><button onClick={() => review(candidate, "reject")}>Не сохранять</button><button className="accept" onClick={() => review(candidate, "accept")}>{candidate.status === "conflict" ? "Заменить старое" : "Сохранить"}</button></footer></article>)}</div></section>}<Button className="primary wide" onClick={beginCreate}><Icon name="plus"/>Добавить память</Button><div className="searchField memorySearch"><Icon name="search"/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти в памяти" aria-label="Поиск по памяти"/></div><div className="memoryList">{visible.length === 0 && <Empty icon="memory" title="Память пуста" text="Добавьте факт вручную или напишите в чате: «Запомни это: …»."/>}{visible.map((item) => <article key={item.id} className={`${item.status} ${item.enabled ? "" : "disabled"}`}><header><span>{item.scope === "global" ? "Все чаты" : item.scope === "project" ? "Проект" : "Чат"}</span><small>{item.memory_type}</small>{item.pinned && <b title="Закреплено">◆</b>}</header><p>{item.content}</p><footer><button onClick={() => beginEdit(item)}>Изменить</button><button onClick={() => action(item, "pin")}>{item.pinned ? "Открепить" : "Закрепить"}</button><button onClick={() => action(item, "archive")}>{item.status === "archived" ? "Вернуть" : "В архив"}</button><button className="dangerText" onClick={() => action(item, "delete")}>Удалить</button></footer></article>)}</div></Drawer>;
}

function WalletPanel({wallet, onClose}: {wallet: Wallet | null; onClose: () => void}) {
  const [amount, setAmount] = useState("500"); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const topup = async () => {setBusy(true); setError(""); try {const payment = await api<{confirmation_url: string}>("/payments/", {method: "POST", headers: {"Idempotency-Key": `web-payment:${crypto.randomUUID()}`}, body: JSON.stringify({amount_rub: amount})}); if (payment.confirmation_url) window.location.assign(payment.confirmation_url);} catch (reason) {setError(reason instanceof Error ? reason.message : "Пополнение временно недоступно");} finally {setBusy(false);}};
  return <Drawer title="Баланс и расходы" onClose={onClose}><div className="walletHero"><small>Доступно</small><strong>{money(wallet?.available_rub)}</strong><div><span>Оплачено: {money(wallet?.paid_rub)}</span><span>Промо: {money(wallet?.promo_rub)}</span><span>В резерве: {money(wallet?.reserved_rub)}</span></div></div><h3>Пополнить баланс</h3><div className="amountGrid">{[300,500,1000,3000].map((item) => <button key={item} className={amount === String(item) ? "active" : ""} onClick={() => setAmount(String(item))}>{item.toLocaleString("ru")} ₽</button>)}</div><label>Другая сумма<input type="number" min="100" max="100000" value={amount} onChange={(event) => setAmount(event.target.value)}/></label>{error && <div className="alert error">{error}</div>}<Button className="primary wide" onClick={topup} disabled={busy}>{busy ? "Создаём платёж…" : `Пополнить на ${money(amount)}`}</Button><p className="safeNote">Баланс не сгорает. Зачисление происходит только после подтверждения платёжной системой.</p><h3>Последние операции</h3><div className="ledger">{wallet?.entries.map((entry) => <div key={entry.id}><span><b>{entry.kind}</b><small>{dateLabel(entry.created_at)}</small></span><strong>{money(entry.amount_rub)}</strong></div>)}</div></Drawer>;
}

function NotificationsPanel({items, onChange, onClose}: {items: Notification[]; onChange: (items: Notification[]) => void; onClose: () => void}) {
  const read = async (item: Notification) => {if (item.read_at) return; const updated = await api<Notification>(`/auth/notifications/${item.id}/read/`, {method: "POST"}); onChange(items.map((current) => current.id === item.id ? updated : current));};
  return <Drawer title="Уведомления" onClose={onClose}><div className="cardList notifications">{items.length === 0 && <Empty icon="bell" title="Уведомлений нет" text="Здесь появятся важные события по балансу и работе сервиса."/>}{items.map((item) => <button key={item.id} className={item.read_at ? "read" : ""} onClick={() => read(item)}><i className={item.level}/><span><b>{item.title}</b><p>{item.body}</p><small>{dateLabel(item.created_at)}</small></span></button>)}</div></Drawer>;
}

function SettingsPanel({user, preferences, onChange, onLogout, onClose}: {user: User; preferences: Preference; onChange: (item: Preference) => void; onLogout: () => void; onClose: () => void}) {
  const [message, setMessage] = useState(""); const [error, setError] = useState("");
  const save = async (event: FormEvent<HTMLFormElement>) => {event.preventDefault(); const data = new FormData(event.currentTarget); try {const updated = await api<Preference>("/auth/preferences/", {method: "PATCH", body: JSON.stringify({low_balance_threshold_rub: data.get("low"), daily_spend_limit_rub: data.get("daily") || null, monthly_spend_limit_rub: data.get("monthly") || null, billing_notifications: data.get("billing") === "on", product_notifications: data.get("product") === "on", memory_enabled: data.get("memory") === "on", auto_memory_enabled: data.get("auto_memory") === "on", auto_memory_default_scope: data.get("auto_memory_scope") || preferences.auto_memory_default_scope})}); onChange(updated); setMessage("Настройки сохранены"); setError("");} catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось сохранить");}};
  const password = async (event: FormEvent<HTMLFormElement>) => {event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); try {await api("/auth/change-password/", {method: "POST", body: JSON.stringify({current_password: data.get("current"), new_password: data.get("next")})}); form.reset(); setMessage("Пароль изменён"); setError("");} catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось изменить пароль");}};
  const logout = async (all = false) => {await api(all ? "/auth/logout-all/" : "/auth/logout/", {method: "POST"}); onClose(); onLogout();};
  return <Drawer title="Аккаунт и настройки" onClose={onClose}><div className="accountCard"><div className="avatar large">{user.username.slice(0,1).toUpperCase()}</div><div><b>{user.username}</b><p>{user.email}</p></div></div>{message && <div className="alert success">{message}</div>}{error && <div className="alert error">{error}</div>}<form className="stack settingsForm" onSubmit={save}><h3>Память, расходы и уведомления</h3><label className="check"><input name="memory" type="checkbox" defaultChecked={preferences.memory_enabled}/>Разрешить использовать мою память</label><div className={`experimentBox ${preferences.auto_memory_available ? "" : "unavailable"}`}><div><b>Предложения памяти</b><span>Эксперимент</span></div><p>Система может находить полезные факты только в ваших сообщениях. Каждый кандидат требует подтверждения.</p><label className="check"><input name="auto_memory" type="checkbox" disabled={!preferences.auto_memory_available} defaultChecked={preferences.auto_memory_enabled}/>Предлагать факты автоматически</label><label>Область по умолчанию<select name="auto_memory_scope" disabled={!preferences.auto_memory_available} defaultValue={preferences.auto_memory_default_scope}><option value="conversation">Только текущий чат</option><option value="project">Текущий проект</option><option value="global">Все чаты</option></select></label>{!preferences.auto_memory_available && <small>Функция пока выключена владельцем сервиса.</small>}</div><label>Предупреждать при балансе ниже<input name="low" type="number" min="0" step="1" defaultValue={preferences.low_balance_threshold_rub}/></label><div className="twoColumns"><label>Лимит в день<input name="daily" type="number" min="0.01" step="0.01" defaultValue={preferences.daily_spend_limit_rub ?? ""} placeholder="Без лимита"/></label><label>Лимит в месяц<input name="monthly" type="number" min="0.01" step="0.01" defaultValue={preferences.monthly_spend_limit_rub ?? ""} placeholder="Без лимита"/></label></div><label className="check"><input name="billing" type="checkbox" defaultChecked={preferences.billing_notifications}/>Уведомления о балансе</label><label className="check"><input name="product" type="checkbox" defaultChecked={preferences.product_notifications}/>Новости продукта</label><Button className="primary">Сохранить</Button></form><form className="stack settingsForm" onSubmit={password}><h3>Безопасность</h3><label>Текущий пароль<input name="current" type="password" autoComplete="current-password" required/></label><label>Новый пароль<input name="next" type="password" minLength={8} autoComplete="new-password" required/></label><Button>Изменить пароль</Button></form><div className="dangerZone"><Button onClick={() => logout(false)}>Выйти</Button><Button className="danger" onClick={() => logout(true)}>Завершить все сессии</Button></div></Drawer>;
}

function SupportPanel({onClose}: {onClose: () => void}) {
  const [sent, setSent] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {event.preventDefault(); const data = new FormData(event.currentTarget); try {await api("/auth/support/", {method: "POST", body: JSON.stringify({subject: data.get("subject"), message: data.get("message")})}); setSent(true);} catch (reason) {setError(reason instanceof Error ? reason.message : "Не удалось отправить обращение");}};
  return <Drawer title="Поддержка" onClose={onClose}>{sent ? <div className="successState"><div>✓</div><h3>Обращение отправлено</h3><p>Мы сохранили запрос и его технический контекст.</p><Button onClick={onClose}>Готово</Button></div> : <form className="stack" onSubmit={submit}><p className="muted">Опишите проблему без паролей, ключей и платёжных секретов.</p><label>Тема<input name="subject" maxLength={160} required/></label><label>Что произошло<textarea name="message" rows={7} required/></label>{error && <div className="alert error">{error}</div>}<Button className="primary">Отправить обращение</Button></form>}</Drawer>;
}

function ContextInspector({context}: {context: GenerationMeta["context"]}) {
  const budget = context.budget ?? {};
  const components = context.components ?? [];
  const citations = context.citations ?? [];
  const labels: Record<string, string> = {system_policy: "Системная политика", project_instruction: "Инструкция проекта", recent_message: "Недавнее сообщение", rolling_summary: "Rolling summary", memory: "Память", old_message: "Старое сообщение", file_chunk: "Фрагмент файла"};
  const used = budget.input_tokens ?? 0;
  const limit = budget.input_limit ?? 0;
  const fill = limit ? Math.min(100, Math.round(used / limit * 100)) : 0;
  const routing = context.routing;
  return <section className="contextInspector">{routing && <div className="routingCard"><header><span>AUTO Router</span><small>{routing.mode === "manual" ? "Вручную" : routing.mode === "economy" ? "Эконом" : routing.mode === "maximum" ? "Максимум" : "Баланс"}</small></header><b>{routing.selected_model}{routing.model_version ? ` · ${routing.model_version}` : ""}</b><p>{routing.explanation}</p><div><span>Уверенность: {Math.round(routing.classification_confidence * 100)}%</span><span>Оценка: до {money(routing.estimated_cost_rub)}</span></div>{routing.candidates.filter((item) => item.status === "eligible").length > 1 && <details><summary>Сравнение кандидатов</summary>{routing.candidates.filter((item) => item.status === "eligible").sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)).slice(0, 5).map((item) => <div className="routeCandidate" key={item.model}><span>#{item.rank} {item.model}{item.model_version ? ` · ${item.model_version}` : ""}</span><b>{item.score == null ? "ручной маршрут" : `${Math.round(item.score * 100)} баллов`}</b></div>)}</details>}</div>}<header><div><h3>Context Snapshot</h3><small>{context.sha256 ? `#${context.sha256.slice(0, 10)}` : "Старый формат snapshot"}</small></div><b>{used.toLocaleString("ru")} / {limit.toLocaleString("ru")}</b></header>{limit > 0 && <div className="budgetBar" title={`Использовано ${fill}%`}><i style={{width: `${fill}%`}}/></div>}<div className="budgetMeta"><span>Резерв ответа: {(budget.output_reserved ?? 0).toLocaleString("ru")}</span><span>Свободно: {(budget.remaining ?? 0).toLocaleString("ru")}</span></div><div className="contextList">{components.length === 0 ? <p className="muted">Детализация недоступна для этого ответа.</p> : components.map((item, index) => <article key={`${item.source_id}-${index}`}><small>{labels[item.kind] ?? item.kind} · {item.tokens} токенов{item.truncated ? " · сокращено" : ""}</small><b>{item.label}</b><p>{item.content}</p>{item.citation && <code>{item.citation.id}</code>}</article>)}</div>{citations.length > 0 && <div className="citationList"><h4>Источники</h4>{citations.map((item) => <div key={item.id}><b>{item.file_name}</b><small>Фрагмент {item.position + 1} · {item.id}</small></div>)}</div>}{(context.dropped_or_deduplicated ?? 0) > 0 && <p className="contextDropped">Не включено или удалено дубликатов: {context.dropped_or_deduplicated}</p>}</section>;
}

function Empty({icon, title, text}: {icon: "search" | "folder" | "file" | "bell" | "memory"; title: string; text: string}) {
  return <div className="emptyState"><span><Icon name={icon}/></span><h3>{title}</h3><p>{text}</p></div>;
}
