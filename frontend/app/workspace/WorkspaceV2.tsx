"use client";

import Link from "next/link";
import {ChangeEvent,useCallback,useEffect,useRef,useState} from "react";
import {useRouter} from "next/navigation";
import {ApiError,api,ensureCsrf,streamMessage} from "../../lib/api";
import {mergeChatMessages,snapshotMessageUpdate} from "../../lib/chat-state";
import {createLatestRequest,createDraftWriter,attachmentReadiness,createAttachmentDrafts} from "../../lib/workspace-state";
import type {AIModel,ChatMessage,Conversation,FileAsset,Project,Wallet} from "../../lib/types";
import {trackProductEvent} from "../components/ProductAnalytics";
import {ActivityTrace,type ActivityStep} from "./ActivityTrace";
import {ChatThread} from "./ChatThread";
import {CostConfirmation,type CostEstimate} from "./CostConfirmation";
import {Composer} from "./Composer";
import {ErrorBoundary} from "./ErrorBoundary";
import {Icon} from "./Icons";
import {SearchOverlay} from "./SearchOverlay";
import {Sidebar} from "./Sidebar";
import type {ConversationFolder,ConversationSummary,DraftState} from "./types";

type WorkspacePage={conversation:Conversation;has_more:boolean;next_before:string|null};
type ServerDraft={content:string;version:number;updated_at:string|null};
type OnboardingStatus={completed_generations:number};
type Running={controller:AbortController};
type RoutingMode="auto"|"manual"|"economy"|"balanced"|"maximum";
type ConversationSettings={id:string;title:string;selected_model:string;routing_mode:RoutingMode;project:string|null;memory_enabled:boolean;updated_at:string};
type DeletedToast={summary:ConversationSummary;wasActive:boolean};

const draftKey=(id:string|null)=>`aiws:draft:${id??"new"}`;
const nowIso=()=>new Date().toISOString();
const imageTypes=new Set(["png","jpeg","webp"]);
const summaryFromConversation=(value:Conversation,existing?:ConversationSummary):ConversationSummary=>({id:value.id,title:value.title,routing_mode:value.routing_mode,selected_model:value.selected_model,project:value.project,folder:existing?.folder??null,is_pinned:existing?.is_pinned??false,created_at:value.created_at,updated_at:value.updated_at});

export default function WorkspaceV2(){
 const router=useRouter();
 const[boot,setBoot]=useState<"loading"|"ready"|"error">("loading");
 const[summaries,setSummaries]=useState<ConversationSummary[]>([]);const[folders,setFolders]=useState<ConversationFolder[]>([]);const[active,setActive]=useState<Conversation|null>(null);const[hasMore,setHasMore]=useState(false);const[nextBefore,setNextBefore]=useState<string|null>(null);const[loadingOlder,setLoadingOlder]=useState(false);
 const[models,setModels]=useState<AIModel[]>([]);const[projects,setProjects]=useState<Project[]>([]);const[wallet,setWallet]=useState<Wallet|null>(null);const[value,setValue]=useState("");const[error,setError]=useState("");const[note,setNote]=useState("Загружаем данные рабочего пространства…");const[offline,setOffline]=useState(false);const[collapsed,setCollapsed]=useState(false);const[searchOpen,setSearchOpen]=useState(false);const[toolsOpen,setToolsOpen]=useState(false);const[attachments,setVisibleAttachments]=useState<FileAsset[]>([]);const[uploading,setUploading]=useState(false);const[runningIds,setRunningIds]=useState<Set<string>>(new Set());const[newRouting,setNewRouting]=useState<{mode:RoutingMode;model:string}>({mode:"auto",model:""});const[newProject,setNewProject]=useState<string|null>(null);const[deletedToast,setDeletedToast]=useState<DeletedToast|null>(null);const[activityByConversation,setActivityByConversation]=useState<Record<string,ActivityStep[]>>({});
 const [mobile,setMobile]=useState(false);const [conversationLoading,setConversationLoading]=useState(false);const [draftReady,setDraftReady]=useState(false);
 const [costEstimate,setCostEstimate]=useState<CostEstimate|null>(null);const costResolver=useRef<((confirmed:boolean)=>void)|null>(null);
 const resolveCost=(confirmed:boolean)=>{costResolver.current?.(confirmed);costResolver.current=null;setCostEstimate(null)};
 const navigation=useRef(createLatestRequest());const draftWriter=useRef(createDraftWriter());const createRef=useRef<Promise<Conversation>|null>(null);
 const runningRef=useRef(new Map<string,Running>());const activeIdRef=useRef<string|null>(null);const valueRef=useRef("");const draftTimer=useRef<number|null>(null);const serverDraftTimer=useRef<number|null>(null);const undoTimer=useRef<number|null>(null);const fileInput=useRef<HTMLInputElement|null>(null);const imageInput=useRef<HTMLInputElement|null>(null);const activeId=active?.id??null;
 const attachmentDrafts=useRef(createAttachmentDrafts<FileAsset>({getItem:key=>localStorage.getItem(key),setItem:(key,value)=>localStorage.setItem(key,value)}));
 const updateAttachments=useCallback((id:string,update:(files:FileAsset[])=>FileAsset[])=>{const next=attachmentDrafts.current.update(id,update);if(activeIdRef.current===id)setVisibleAttachments(next)},[]);
 const setAttachments=useCallback((update:(files:FileAsset[])=>FileAsset[])=>{const id=activeIdRef.current;if(id)updateAttachments(id,update)},[updateAttachments]);
 const attachmentState=attachmentReadiness(attachments);
 const pendingFileIds=attachments.filter(item=>!["ready","partial","failed","deleted","deleting"].includes(item.status)).map(item=>item.id).join(",");
 const runningCurrent=activeId?runningIds.has(activeId):false;
 useEffect(()=>{activeIdRef.current=activeId},[activeId]);
 useEffect(()=>{valueRef.current=value},[value]);

 const recordActivity=useCallback((conversationId:string,next:ActivityStep)=>{setActivityByConversation(current=>{const previous=current[conversationId]??[];const rows=previous.map(item=>((item.state==="running"||item.state==="streaming")&&item.step!==next.step?{...item,state:"completed"}:item));const same=rows.findIndex(item=>item.step===next.step&&item.message===next.message);if(same>=0){rows[same]={...rows[same],...next};return {...current,[conversationId]:rows.slice(-10)}}const sameRunning=[...rows].reverse().findIndex(item=>item.step===next.step&&(item.state==="running"||item.state==="streaming"));if(sameRunning>=0){const index=rows.length-1-sameRunning;rows[index]={...rows[index],...next};return {...current,[conversationId]:rows.slice(-10)}}return {...current,[conversationId]:[...rows,next].slice(-10)}})},[]);
 const persistLocal=(id:string|null,content:string)=>{try{localStorage.setItem(draftKey(id),JSON.stringify({content,updatedAt:Date.now(),serverVersion:0} satisfies DraftState));}catch{}};
 const loadFolders=useCallback(async()=>setFolders(await api<ConversationFolder[]>("/conversation-folders/")),[]);
 const loadSummaries=useCallback(async()=>setSummaries(await api<ConversationSummary[]>("/conversation-summaries/?limit=120")),[]);
 const loadConversation=useCallback(async(id:string,replaceDraft=true)=>{
  const ticket=navigation.current.begin();setConversationLoading(true);setDraftReady(false);setError("");
  try{
   const page=await api<WorkspacePage>(`/conversation-workspace/${id}/?limit=60`);
   if(!navigation.current.isCurrent(ticket))return;
   let content="";
   if(replaceDraft){
    let local:DraftState|null=null;try{const raw=localStorage.getItem(draftKey(id));if(raw)local=JSON.parse(raw) as DraftState;}catch{}
    let server:ServerDraft|null=null;try{server=await api<ServerDraft>(`/conversations/${id}/draft/`);}catch{}
    if(!navigation.current.isCurrent(ticket))return;
    const serverTime=server?.updated_at?new Date(server.updated_at).getTime():0;
    content=local&&local.updatedAt>=serverTime?local.content:server?.content??"";
   }
   const restoredFiles=await Promise.all(attachmentDrafts.current.ids(id).map(async fileId=>{
    try{return await api<FileAsset>(`/files/${encodeURIComponent(fileId)}/`)}catch(reason){if(reason instanceof ApiError&&[403,404,410].includes(reason.status))return null;throw reason}
   }));
   if(!navigation.current.isCurrent(ticket))return;
   const selected=attachmentDrafts.current.update(id,()=>restoredFiles.filter((file):file is FileAsset=>file!==null));
   activeIdRef.current=id;setActive(page.conversation);setHasMore(page.has_more);setNextBefore(page.next_before);
   if(replaceDraft){valueRef.current=content;setValue(content)}setVisibleAttachments(selected);
  }catch(reason){if(navigation.current.isCurrent(ticket))setError(reason instanceof Error?reason.message:"Не удалось открыть чат. Попробуйте ещё раз.")}
  finally{if(navigation.current.isCurrent(ticket)){setConversationLoading(false);setDraftReady(true)}}
 },[]);
 const load=useCallback(async()=>{setError("");setNote("Загружаем данные рабочего пространства…");try{await ensureCsrf();await api("/auth/me/");const results=await Promise.allSettled([api<ConversationSummary[]>("/conversation-summaries/?limit=120"),api<ConversationFolder[]>("/conversation-folders/"),api<AIModel[]>("/models/"),api<Project[]>("/projects/"),api<Wallet>("/wallet/")]);const summaryRows=results[0].status==="fulfilled"?results[0].value:[];const folderRows=results[1].status==="fulfilled"?results[1].value:[];const modelRows=results[2].status==="fulfilled"?results[2].value:[];const projectRows=results[3].status==="fulfilled"?results[3].value:[];const walletData=results[4].status==="fulfilled"?results[4].value:null;setSummaries(summaryRows);setFolders(folderRows);setModels(modelRows);setProjects(projectRows);setWallet(walletData);setNewRouting(current=>({...current,model:current.model||modelRows.find(item=>item.available)?.slug||""}));setBoot("ready");try{const stored=localStorage.getItem("aiws:sidebar-collapsed");setCollapsed(window.innerWidth<=820?true:stored==="1");}catch{}const requestedProject=new URLSearchParams(window.location.search).get("project");if(requestedProject&&projectRows.some(project=>project.id===requestedProject&&!project.archived_at)){setNewProject(requestedProject);setActive(null);try{const raw=localStorage.getItem(draftKey(null));if(raw)setValue((JSON.parse(raw) as DraftState).content);}catch{}}else if(summaryRows[0]){await loadConversation(summaryRows[0].id);}else{setActive(null);try{const raw=localStorage.getItem(draftKey(null));if(raw)setValue((JSON.parse(raw) as DraftState).content);}catch{}}setDraftReady(true);const degraded=results.filter(item=>item.status==="rejected").length;setNote(degraded?"Часть данных не загрузилась. Проверьте соединение и обновите страницу.":"");}catch(reason){if(reason instanceof ApiError&&[401,403].includes(reason.status)){router.replace("/login");return;}setError(reason instanceof Error?reason.message:"Не удалось установить защищённое соединение");setNote("");setBoot("error");}},[router,loadConversation]);
 useEffect(()=>{void load();},[load]);
 useEffect(()=>{const refresh=()=>{if(document.visibilityState!=="hidden")void api<Wallet>("/wallet/").then(setWallet).catch(()=>undefined)};const timer=window.setInterval(refresh,30000);window.addEventListener("focus",refresh);document.addEventListener("visibilitychange",refresh);return()=>{window.clearInterval(timer);window.removeEventListener("focus",refresh);document.removeEventListener("visibilitychange",refresh)}},[]);
 useEffect(()=>{if(!pendingFileIds)return;const controller=new AbortController();let polling=false;const poll=async()=>{if(polling||document.visibilityState==="hidden"||!navigator.onLine)return;polling=true;try{const results=await Promise.allSettled(pendingFileIds.split(",").map(id=>api<FileAsset>(`/files/${id}/`,{signal:controller.signal})));if(controller.signal.aborted)return;setAttachments(current=>current.map(item=>{const result=results.find(result=>result.status==="fulfilled"&&result.value.id===item.id);return result?.status==="fulfilled"?result.value:item}))}finally{polling=false}};const timer=window.setInterval(()=>void poll(),2500);return()=>{controller.abort();window.clearInterval(timer)}},[pendingFileIds]);
 useEffect(()=>{const viewport=window.visualViewport;const update=()=>{setMobile(window.innerWidth<=820);document.documentElement.style.setProperty("--chat-viewport-height",`${viewport?.height??window.innerHeight}px`)};update();viewport?.addEventListener("resize",update);window.addEventListener("resize",update);return()=>{viewport?.removeEventListener("resize",update);window.removeEventListener("resize",update);document.documentElement.style.removeProperty("--chat-viewport-height")}},[]);
 useEffect(()=>{if(!toolsOpen)return;const close=(event:PointerEvent)=>{if(!(event.target as HTMLElement).closest(".composerWrap"))setToolsOpen(false)};const key=(event:KeyboardEvent)=>{if(event.key==="Escape"){setToolsOpen(false);window.dispatchEvent(new Event("aiws:focus-composer"))}};document.addEventListener("pointerdown",close);window.addEventListener("keydown",key);return()=>{document.removeEventListener("pointerdown",close);window.removeEventListener("keydown",key)}},[toolsOpen]);
 useEffect(()=>{const online=()=>setOffline(false);const offlineHandler=()=>setOffline(true);setOffline(!navigator.onLine);window.addEventListener("online",online);window.addEventListener("offline",offlineHandler);return()=>{window.removeEventListener("online",online);window.removeEventListener("offline",offlineHandler)}},[]);
 useEffect(()=>{const starter=sessionStorage.getItem("starter-prompt");if(starter){setValue(starter);sessionStorage.removeItem("starter-prompt");}},[]);
 useEffect(()=>{const shortcut=(event:KeyboardEvent)=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){event.preventDefault();setSearchOpen(true)}};window.addEventListener("keydown",shortcut);return()=>window.removeEventListener("keydown",shortcut)},[]);
 useEffect(()=>{const persistNow=()=>persistLocal(activeIdRef.current,valueRef.current);window.addEventListener("pagehide",persistNow);return()=>window.removeEventListener("pagehide",persistNow)},[]);
 useEffect(()=>{if(boot!=="ready"||!draftReady||conversationLoading)return;if(draftTimer.current)window.clearTimeout(draftTimer.current);draftTimer.current=window.setTimeout(()=>persistLocal(activeId,value),300);if(activeId){if(serverDraftTimer.current)window.clearTimeout(serverDraftTimer.current);serverDraftTimer.current=window.setTimeout(()=>{void draftWriter.current(activeId,()=>api(`/conversations/${activeId}/draft/`,{method:"PUT",body:JSON.stringify({content:value})})).catch(()=>undefined)},1400);}return()=>{if(draftTimer.current)window.clearTimeout(draftTimer.current);if(serverDraftTimer.current)window.clearTimeout(serverDraftTimer.current)}},[value,activeId,boot,draftReady,conversationLoading]);
 useEffect(()=>()=>{costResolver.current?.(false);if(undoTimer.current)window.clearTimeout(undoTimer.current)},[]);

 const clearDraft=async(id:string|null)=>{if(activeIdRef.current===id&&valueRef.current.trim())return;try{localStorage.removeItem(draftKey(id));}catch{}if(id)await draftWriter.current(id,()=>api(`/conversations/${id}/draft/`,{method:"DELETE"})).catch(()=>undefined)};
 const restoreBlankDraft=()=>{try{const raw=localStorage.getItem(draftKey(null));setValue(raw?(JSON.parse(raw) as DraftState).content:"");}catch{setValue("")}};
 const newChat=()=>{navigation.current.invalidate();setConversationLoading(false);setDraftReady(true);persistLocal(activeId,value);setActive(null);activeIdRef.current=null;setHasMore(false);setNextBefore(null);setVisibleAttachments([]);setError("");setNote("");restoreBlankDraft();window.dispatchEvent(new Event("aiws:focus-composer"))};
 const createConversation=async()=>{if(createRef.current)return createRef.current;const ticket=navigation.current.begin();const task=(async()=>{const payload:Record<string,unknown>={title:"Новый чат",routing_mode:newRouting.mode,project:newProject};if(newRouting.mode==="manual"&&newRouting.model)payload.selected_model=newRouting.model;const conversation=await api<Conversation>("/conversations/",{method:"POST",body:JSON.stringify(payload)});const summary=summaryFromConversation(conversation);setSummaries(current=>[summary,...current.filter(x=>x.id!==conversation.id)]);if(navigation.current.isCurrent(ticket)){setActive(conversation);setHasMore(false);setNextBefore(null);activeIdRef.current=conversation.id;}return conversation;})();createRef.current=task;try{return await task}finally{createRef.current=null}};
 const openChat=async(id:string)=>{if(activeId===id&&!conversationLoading)return;persistLocal(activeId,value);await loadConversation(id);window.dispatchEvent(new Event("aiws:focus-composer"))};
 const mergeConversation=(conversation:Conversation)=>{setActive(current=>{if(!current||current.id!==conversation.id)return current;return {...conversation,messages:mergeChatMessages(current.messages,conversation.messages)};});setSummaries(current=>{const existing=current.find(x=>x.id===conversation.id);const next=summaryFromConversation(conversation,existing);return [next,...current.filter(x=>x.id!==conversation.id)]})};
 const refreshConversation=async(id:string)=>{const page=await api<WorkspacePage>(`/conversation-workspace/${id}/?limit=60`);if(activeIdRef.current===id){mergeConversation(page.conversation);setHasMore(page.has_more);setNextBefore(page.next_before)}else{setSummaries(current=>{const existing=current.find(x=>x.id===id);const next=summaryFromConversation(page.conversation,existing);return [next,...current.filter(x=>x.id!==id)]})}};
 const loadOlder=async()=>{if(!active||!hasMore||!nextBefore||loadingOlder)return;setLoadingOlder(true);try{const page=await api<WorkspacePage>(`/conversation-workspace/${active.id}/?limit=60&before=${encodeURIComponent(nextBefore)}`);setActive(current=>current&&current.id===active.id?{...current,messages:[...page.conversation.messages,...current.messages]}:current);if(activeIdRef.current===active.id){setHasMore(page.has_more);setNextBefore(page.next_before)}}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить историю. Попробуйте ещё раз.")}finally{setLoadingOlder(false)}};
 const applySettings=(settings:ConversationSettings)=>{setActive(current=>current&&current.id===settings.id?{...current,title:settings.title,selected_model:settings.selected_model,routing_mode:settings.routing_mode,project:settings.project,memory_enabled:settings.memory_enabled,updated_at:settings.updated_at}:current);setSummaries(current=>current.map(item=>item.id===settings.id?{...item,title:settings.title,selected_model:settings.selected_model,routing_mode:settings.routing_mode,project:settings.project,updated_at:settings.updated_at}:item))};
 const updateConversation=async(changes:Record<string,unknown>)=>{if(!active){if("routing_mode" in changes)setNewRouting(r=>({...r,mode:String(changes.routing_mode) as RoutingMode}));if("selected_model" in changes)setNewRouting(r=>({...r,mode:"manual",model:String(changes.selected_model)}));if("project" in changes)setNewProject(changes.project?String(changes.project):null);return}try{const result=await api<ConversationSettings>(`/conversation-settings/${active.id}/`,{method:"PATCH",body:JSON.stringify(changes)});applySettings(result)}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось изменить чат")}};
 const trackActivation=async()=>{try{const onboarding=await api<OnboardingStatus>("/auth/onboarding/");if(onboarding.completed_generations===1)void trackProductEvent("first_chat");if(onboarding.completed_generations===2)void trackProductEvent("second_chat")}catch{}};

 const send=async()=>{
  const prompt=value.trim();if(!prompt||offline||uploading||attachmentState.blocked||conversationLoading)return;const sentAttachments=[...attachments];const sourceWasNew=!active;
  setError("");setNote("");let conversation=active;let accepted=false;let cancelled=false;
  let assistantId="";let optimisticUserId="";let pending="";let timer:number|null=null;
  const updateAssistant=(changes:Partial<ChatMessage>)=>setActive(current=>current&&current.id===conversation?.id?{...current,messages:current.messages.map(message=>message.id===assistantId?{...message,...changes}:message)}:current);
  const flush=()=>{timer=null;if(!pending)return;const chunk=pending;pending="";setActive(current=>current&&current.id===conversation?.id?{...current,messages:current.messages.map(message=>message.id===assistantId?{...message,content:message.content+chunk}:message)}:current)};
  try{
   if(!conversation)conversation=await createConversation();
   if(!conversation||runningRef.current.has(conversation.id))return;
   const conversationId=conversation.id;
   const clientMessageId=crypto.randomUUID();
   optimisticUserId=`local-user-${clientMessageId}`;
   setActivityByConversation(current=>({...current,[conversationId]:[{step:"prepare",state:"running",message:"Подготавливаю запрос…"}]}));
   const userMessage:ChatMessage={id:optimisticUserId,client_message_id:clientMessageId,branch:conversation.active_branch,role:"user",content:prompt,status:"saved",generation:null,created_at:nowIso()};
   assistantId=`local-ai-${crypto.randomUUID()}`;
   const assistant:ChatMessage={id:assistantId,branch:conversation.active_branch,role:"assistant",content:"",status:"streaming",generation:null,created_at:nowIso()};
   setActive(current=>current&&current.id===conversationId?{...current,messages:[...current.messages,userMessage,assistant]}:current);
   if(activeIdRef.current===conversationId){setValue("");valueRef.current="";}if(sourceWasNew){try{localStorage.removeItem(draftKey(null))}catch{}}
   const controller=new AbortController();runningRef.current.set(conversationId,{controller});
   setRunningIds(current=>new Set(current).add(conversationId));
   const schedule=()=>{if(timer!==null)return;timer=window.setTimeout(flush,document.visibilityState==="hidden"?300:50)};
   await streamMessage(conversationId,{content:prompt,client_message_id:clientMessageId,file_ids:sentAttachments.map(item=>item.id)},`workspace:${crypto.randomUUID()}`,({event,data})=>{
    if(event==="generation"){
     if(!accepted){accepted=true;void clearDraft(conversationId)}
     updateAssistant({pending_generation_id:String(data.id??data.generation_id??"")});
     setActive(current=>current&&current.id===conversationId?{...current,messages:current.messages.map(message=>message.id===userMessage.id?{...message,client_message_id:String(data.client_message_id??clientMessageId)}:message)}:current);
    }
    if(event==="activity")recordActivity(conversationId,{step:String(data.step??"work"),state:String(data.state??"running"),message:String(data.message??"Выполняю задачу…"),source_count:typeof data.source_count==="number"?data.source_count:undefined});
    if(event==="delta"){pending+=String(data.text??"");schedule()}
    if(event==="snapshot"){
     if(timer!==null){window.clearTimeout(timer);timer=null}pending="";
     updateAssistant(snapshotMessageUpdate(data));
    }
    if(["completed","cancelled","error"].includes(event)){
     if(timer!==null){window.clearTimeout(timer);timer=null}flush();
     setActive(current=>current&&current.id===conversationId?{...current,messages:current.messages.map(message=>message.id===assistantId?{...message,status:event==="completed"?"completed":message.content?"partial":"failed"}:message)}:current);
    }
    if(activeIdRef.current===conversationId){
     if(event==="recovery"){const message=data.action==="fallback"?"Подключаю резервную модель…":"Повторяю запрос к провайдеру…";setNote(message);recordActivity(conversationId,{step:"recovery",state:"warning",message})}
     if(event==="routing")setNote(String(data.explanation??"Маршрут выбран"));
     if(event==="error"){const message=String(data.message??"Не удалось завершить ответ. Состояние запроса сохранено.");setError(message);recordActivity(conversationId,{step:"error",state:"failed",message})}
    }
   },controller.signal,estimate=>new Promise<boolean>(resolve=>{
    const finish=(confirmed:boolean)=>{controller.signal.removeEventListener("abort",cancel);resolve(confirmed)};
    const cancel=()=>{costResolver.current=null;setCostEstimate(null);finish(false)};
    if(controller.signal.aborted){resolve(false);return}costResolver.current=finish;setCostEstimate(estimate);controller.signal.addEventListener("abort",cancel,{once:true});
   }));
  }catch(reason){
   cancelled=reason instanceof DOMException&&reason.name==="AbortError";
   if(!accepted){const targetId=conversation?.id??null;if(activeIdRef.current===targetId){setValue(current=>{const restored=current.trim()?current:prompt;persistLocal(targetId,restored);return restored})}else persistLocal(targetId,prompt);setActive(current=>current&&current.id===targetId?{...current,messages:current.messages.filter(message=>message.id!==assistantId&&message.id!==optimisticUserId)}:current)}
   if(reason instanceof ApiError&&reason.status===499)cancelled=true;
   if(cancelled&&!accepted&&conversation)setActivityByConversation(current=>{const next={...current};delete next[conversation!.id];return next});
   if(!cancelled){if(!conversation||activeIdRef.current===conversation.id)setError(reason instanceof Error?reason.message:"Соединение прервалось. Черновик сохранён.");if(conversation)recordActivity(conversation.id,{step:"connection",state:"failed",message:reason instanceof Error?reason.message:"Соединение прервалось. Ответ сохранён на сервере."})}
  }finally{
   if(timer!==null)window.clearTimeout(timer);flush();
   if(conversation){
    runningRef.current.delete(conversation.id);setRunningIds(current=>{const next=new Set(current);next.delete(conversation!.id);return next});
    if(activeIdRef.current===conversation.id)setNote("");
    if(cancelled)await new Promise(resolve=>window.setTimeout(resolve,150));
    await Promise.allSettled([refreshConversation(conversation.id),api<Wallet>("/wallet/").then(setWallet),loadSummaries()]);
    if(accepted)updateAttachments(conversation.id,current=>current.filter(item=>!sentAttachments.some(sent=>sent.id===item.id)));
    await trackActivation();
   }
  }
 };

 const stop=()=>{if(!activeId)return;const live=runningRef.current.get(activeId);if(live)live.controller.abort();else window.dispatchEvent(new CustomEvent("aiws:stop-generation",{detail:{conversationId:activeId}}))};

 const renameChat=async(id:string,title:string)=>{const result=await api<ConversationSettings>(`/conversation-settings/${id}/`,{method:"PATCH",body:JSON.stringify({title})});applySettings(result)};
 const patchUI=async(id:string,changes:{folder?:string|null;is_pinned?:boolean})=>{await api(`/conversation-ui/${id}/`,{method:"PATCH",body:JSON.stringify(changes)});setSummaries(current=>current.map(item=>item.id===id?{...item,...changes}:item));await loadFolders()};
 const deleteChat=async(id:string)=>{const snapshot=summaries.find(item=>item.id===id);if(!snapshot)return;const wasActive=active?.id===id;await api(`/conversations/${id}/`,{method:"DELETE"});setSummaries(current=>current.filter(item=>item.id!==id));if(wasActive){setActive(null);activeIdRef.current=null;setHasMore(false);setNextBefore(null);setVisibleAttachments([]);restoreBlankDraft()}setActivityByConversation(current=>{const next={...current};delete next[id];return next});await loadFolders();if(undoTimer.current)window.clearTimeout(undoTimer.current);setDeletedToast({summary:snapshot,wasActive});undoTimer.current=window.setTimeout(()=>setDeletedToast(null),8000)};
 const undoDelete=async()=>{if(!deletedToast)return;const snapshot=deletedToast;if(undoTimer.current)window.clearTimeout(undoTimer.current);setDeletedToast(null);try{await api(`/conversation-ui/${snapshot.summary.id}/`,{method:"PATCH",body:JSON.stringify({deleted:false})});setSummaries(current=>[snapshot.summary,...current.filter(item=>item.id!==snapshot.summary.id)]);await loadFolders();if(snapshot.wasActive)await loadConversation(snapshot.summary.id)}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось восстановить чат")}};
 const createFolder=async(name:string)=>{await api("/conversation-folders/",{method:"POST",body:JSON.stringify({name})});await loadFolders()};
 const renameFolder=async(id:string,name:string)=>{await api(`/conversation-folders/${id}/`,{method:"PATCH",body:JSON.stringify({name})});await loadFolders()};
 const pinFolder=async(id:string,is_pinned:boolean)=>{await api(`/conversation-folders/${id}/`,{method:"PATCH",body:JSON.stringify({is_pinned})});await loadFolders()};
 const deleteFolder=async(id:string)=>{if(!window.confirm("Удалить папку? Чаты останутся в истории."))return;await api(`/conversation-folders/${id}/`,{method:"DELETE"});setSummaries(current=>current.map(item=>item.folder===id?{...item,folder:null}:item));await loadFolders()};
 const uploadFile=async(event:ChangeEvent<HTMLInputElement>)=>{const file=event.target.files?.[0];event.target.value="";if(!file)return;if(attachments.length>=4){setError("Можно приложить до четырёх файлов. Уберите один, чтобы добавить другой.");return;}setUploading(true);setError("");try{let current=active;if(!current)current=await createConversation();let projectId=current.project??newProject;let targetProject=projectId?projects.find(project=>project.id===projectId&&!project.archived_at):undefined;if(!targetProject)targetProject=projects.find(project=>project.name==="Файлы чата"&&!project.archived_at&&project.role!=="viewer");if(!targetProject){targetProject=await api<Project>("/projects/",{method:"POST",body:JSON.stringify({name:"Файлы чата"})});setProjects(existing=>[targetProject!,...existing.filter(project=>project.id!==targetProject!.id)])}projectId=targetProject.id;if(current.project!==projectId){const updated=await api<Conversation>(`/conversations/${current.id}/`,{method:"PATCH",body:JSON.stringify({project:projectId})});setActive(existing=>existing?.id===updated.id?updated:existing);setSummaries(existing=>existing.map(item=>item.id===updated.id?summaryFromConversation(updated,item):item));current=updated;}const form=new FormData();form.append("file",file);form.append("project",projectId);const asset=await api<FileAsset>("/files/",{method:"POST",headers:{"Idempotency-Key":`upload:${crypto.randomUUID()}`},body:form});updateAttachments(current.id,existing=>[...existing,asset]);void trackProductEvent("file_uploaded",{type:asset.detected_type});return current.id}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить файл");return false}finally{setUploading(false);setToolsOpen(false)}};
 const uploadImage=async(event:ChangeEvent<HTMLInputElement>)=>{const uploaded=await uploadFile(event);if(!uploaded||activeIdRef.current!==uploaded)return;setValue(current=>current.trim()?current:"Проанализируй это изображение. Опиши важные детали и ответь на то, что может быть полезно пользователю.")};
 const ensureConversation=async()=>{const current=active??await createConversation();return current.id};
 const toggleSidebar=()=>setCollapsed(current=>{const next=!current;try{if(window.innerWidth>820)localStorage.setItem("aiws:sidebar-collapsed",next?"1":"0");}catch{}return next});
 const selectorValue=active?(active.routing_mode==="manual"?`model:${active.selected_model}`:`auto:${active.routing_mode}`):(newRouting.mode==="manual"?`model:${newRouting.model}`:`auto:${newRouting.mode}`);
 const projectValue=active?.project??newProject??"";
 const visibleActivity=activeId?(activityByConversation[activeId]??[]):[];

 if(boot==="loading")return <main className="workspaceLoading" role="status"><span className="loadingDot"/><p>Открываем ваши чаты…</p></main>;
 if(boot==="error")return <main className="workspaceLoading"><h1>Не удалось подключиться</h1><p>{error}</p><button onClick={()=>{setBoot("loading");void load()}}>Повторить</button></main>;
 return <main className={`workspaceApp ${collapsed?"sidebarCollapsed":""}`}>
  <ErrorBoundary><Sidebar mobile={mobile} onError={setError} items={summaries} folders={folders} activeId={activeId} wallet={wallet} collapsed={collapsed} runningIds={runningIds} onToggle={toggleSidebar} onCreate={newChat} onSelect={id=>void openChat(id)} onSearch={()=>setSearchOpen(true)} onCreateFolder={createFolder} onRenameFolder={renameFolder} onPinFolder={pinFolder} onDeleteFolder={deleteFolder} onRenameChat={renameChat} onPinChat={(id,pinned)=>patchUI(id,{is_pinned:pinned})} onMoveChat={(id,folder)=>patchUI(id,{folder})} onDeleteChat={deleteChat}/></ErrorBoundary>
  <section className="workspaceMain" inert={mobile&&!collapsed?true:undefined} aria-busy={conversationLoading}><header className="chatHeader"><button className="iconButton mobileChatMenu" type="button" aria-label="Открыть меню чатов" aria-expanded={!collapsed} onClick={toggleSidebar}><Icon name="panel"/></button><div className="chatTitle">{active?<button onClick={()=>window.dispatchEvent(new CustomEvent("aiws:rename-chat",{detail:{id:active.id,title:active.title}}))}>{active.title}</button>:<span>Новый чат</span>}</div><div className="headerControls">{activeId&&<button type="button" className="iconButton chatAssetsButton" aria-label="Поиск и материалы чата" onClick={()=>window.dispatchEvent(new CustomEvent("aiws:open-chat-assets",{detail:{conversationId:activeId}}))}><Icon name="search" size={18}/></button>}<label className="selectControl projectSelect"><select aria-label="Проект чата" disabled={runningCurrent||conversationLoading} value={projectValue} onChange={e=>void updateConversation({project:e.target.value||null})}><option value="">Без проекта</option>{projects.filter(project=>!project.archived_at&&project.role!=="viewer").map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select><Icon name="chevron" size={14}/></label><Link className="headerIcon" href="/app/usage" title="Использование"><Icon name="wallet"/></Link></div></header>
   {error&&<div className="topNotice error"><span role="alert">{error}</span><button aria-label="Закрыть уведомление" onClick={()=>setError("")}><Icon name="x"/></button></div>}{offline&&<div className="topNotice"><span>Нет соединения. Текст сохраняется на устройстве.</span></div>}{note&&!visibleActivity.length&&<div className="topNotice subtle"><span>{note}</span></div>}
   {conversationLoading&&<div className="chatLoadingStatus" role="status">Открываем чат…</div>}
   <ActivityTrace steps={visibleActivity} running={runningCurrent}/>
   <ErrorBoundary><ChatThread busy={runningCurrent||conversationLoading} conversation={active} hasMore={hasMore} loadingOlder={loadingOlder} onLoadOlder={()=>void loadOlder()} onConversation={mergeConversation} onStarter={prompt=>{setValue(prompt);window.dispatchEvent(new Event("aiws:focus-composer"))}}/></ErrorBoundary>
   {attachments.length>0&&<div className="attachmentTray">{attachments.map(item=>{const isImage=imageTypes.has(item.detected_type);return <div key={item.id} className={`attachmentChip ${isImage?"imageAttachment":""}`}><Icon name={isImage?"spark":"folder"} size={15}/><span>{item.original_name}</span><small>{item.status==="ready"?"Готов":item.status==="partial"?"Обработан частично":item.status==="failed"?"Ошибка обработки":"Обрабатывается"}</small><button aria-label={`Убрать файл ${item.original_name}`} onClick={()=>setAttachments(current=>current.filter(x=>x.id!==item.id))}><Icon name="x" size={14}/></button></div>})}</div>}
   <div className="composerWrap"><div className="toolAnchor">{toolsOpen&&<div className="toolMenu"><button disabled={uploading} onClick={()=>fileInput.current?.click()}><Icon name="folderPlus"/>{uploading?"Загружаем…":"Добавить файл"}</button><button disabled={uploading} onClick={()=>imageInput.current?.click()}><Icon name="spark"/>Фото или изображение</button><button onClick={()=>{setValue(current=>current||"Сравни приложенные изображения: выдели ключевые различия, сильные и слабые стороны и сделай вывод.");imageInput.current?.click();setToolsOpen(false)}}><Icon name="scale"/>Сравнить изображения</button><button onClick={()=>{setValue(current=>current||"Найди актуальную информацию в интернете и укажи источники: ");setToolsOpen(false)}}><Icon name="search"/>Поиск в интернете</button></div>}<input ref={fileInput} type="file" hidden onChange={uploadFile}/><input ref={imageInput} type="file" hidden accept="image/png,image/jpeg,image/webp" onChange={event=>void uploadImage(event)}/></div><ErrorBoundary><Composer value={value} setValue={setValue} sending={runningCurrent} disabled={conversationLoading} submitDisabled={uploading||attachmentState.blocked} statusText={uploading?"Загружаем файл…":attachmentState.message} offline={offline} onSend={send} onStop={stop} onOpenTools={()=>setToolsOpen(current=>!current)} modelValue={selectorValue} models={models} onModelChange={next=>{const[kind,val]=next.split(":");void(kind==="auto"?updateConversation({routing_mode:val}):updateConversation({routing_mode:"manual",selected_model:val}))}} conversationId={activeId} ensureConversation={ensureConversation} onAttachImage={()=>imageInput.current?.click()}/></ErrorBoundary></div>
  </section>
  <CostConfirmation estimate={costEstimate} onConfirm={()=>resolveCost(true)} onCancel={()=>resolveCost(false)}/>
  <SearchOverlay open={searchOpen} onClose={()=>setSearchOpen(false)} onOpenConversation={id=>void openChat(id)}/>
  {deletedToast&&<div className="undoToast" role="status"><span>Чат удалён</span><button onClick={()=>void undoDelete()}>Отменить</button><button className="undoClose" aria-label="Закрыть" onClick={()=>setDeletedToast(null)}><Icon name="x" size={14}/></button></div>}
 </main>;
}
