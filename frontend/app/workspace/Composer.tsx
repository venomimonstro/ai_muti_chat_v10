"use client";

import {KeyboardEvent,useEffect,useRef,useState} from "react";
import {api} from "../../lib/api";
import type {AIModel} from "../../lib/types";
import {ChatImageStudio} from "./ChatImageStudio";
import {Icon} from "./Icons";
import {ModelPicker} from "./ModelPicker";

const MAX_MESSAGE_CHARS=100000;
const COMPOSER_MIN_HEIGHT=88;
const COMPOSER_MAX_HEIGHT=320;
const MODEL_CATALOG_EVENT="aiws:model-catalog";

type ComposerProps={
 value:string;
 setValue:(value:string)=>void;
 sending:boolean;
 offline:boolean;
 onSend:()=>void|Promise<void>;
 onStop:()=>void;
 onOpenTools:()=>void;
 modelValue?:string;
 models?:AIModel[];
 onModelChange?:(value:string)=>void;
 conversationId?:string|null;
 ensureConversation?:()=>Promise<string|null>;
 onAttachImage?:()=>void;
 sourceImageId?:string|null;
};

function pendingStreamIdempotencyKey(conversationId:string|null|undefined){
 if(typeof window==="undefined"||!conversationId)return"";
 try{const raw=localStorage.getItem(`aiws:pending-stream:${conversationId}`);if(!raw)return"";const parsed=JSON.parse(raw) as {idempotencyKey?:unknown};return typeof parsed.idempotencyKey==="string"?parsed.idempotencyKey:""}catch{return""}
}

export function Composer({value,setValue,sending,offline,onSend,onStop,onOpenTools,modelValue,models,onModelChange,conversationId,ensureConversation,onAttachImage,sourceImageId}:ComposerProps){
 const ref=useRef<HTMLTextAreaElement|null>(null);const[focused,setFocused]=useState(false);const[slow,setSlow]=useState(false);const[controlValue,setControlValue]=useState(modelValue??"auto:auto");const[controlModels,setControlModels]=useState<AIModel[]>(models??[]);const[catalogState,setCatalogState]=useState<"loading"|"ready"|"error">("loading");const[workspaceModelsAvailable,setWorkspaceModelsAvailable]=useState<boolean|null>(null);const[imageStudioOpen,setImageStudioOpen]=useState(false);const submitGate=useRef(false);
 const trimmed=value.trim();const tooLong=value.length>MAX_MESSAGE_CHARS;const nearLimit=value.length>90000;
 const explicitModelControl=modelValue!==undefined&&models!==undefined&&onModelChange!==undefined;
 const effectiveControlValue=explicitModelControl?(modelValue??"auto:auto"):controlValue;
 const selectedManualSlug=explicitModelControl&&effectiveControlValue.startsWith("model:")?effectiveControlValue.slice(6):"";
 const selectedManualModel=selectedManualSlug?controlModels.find(item=>item.slug===selectedManualSlug):undefined;
 const selectedModelUnavailable=Boolean(selectedManualSlug&&(!selectedManualModel||!selectedManualModel.available));
 // The visible ModelPicker owns /models/ refresh and broadcasts one authoritative
 // client snapshot. A transient catalog error never blocks AUTO; backend readiness is
 // still the final authority when the request is prepared.
 const noModelsAvailable=workspaceModelsAvailable===false;
 useEffect(()=>{const el=ref.current;if(!el)return;el.style.height="0px";const next=Math.min(Math.max(el.scrollHeight,COMPOSER_MIN_HEIGHT),COMPOSER_MAX_HEIGHT);el.style.height=`${next}px`;el.style.overflowY=el.scrollHeight>COMPOSER_MAX_HEIGHT?"auto":"hidden";},[value]);
 useEffect(()=>{if(!sending){setSlow(false);return;}const timer=window.setTimeout(()=>setSlow(true),30000);return()=>window.clearTimeout(timer)},[sending]);
 useEffect(()=>{if(!trimmed)submitGate.current=false},[trimmed]);
 useEffect(()=>{if(modelValue!==undefined)setControlValue(modelValue)},[modelValue]);
 useEffect(()=>{if(models!==undefined)setControlModels(models)},[models]);
 useEffect(()=>{const catalog=(event:Event)=>{const detail=(event as CustomEvent<{state?:string;available?:boolean}>).detail;if(detail?.state==="ready"){setCatalogState("ready");if(typeof detail.available==="boolean")setWorkspaceModelsAvailable(detail.available)}else if(detail?.state==="error"){setCatalogState("error")}};window.addEventListener(MODEL_CATALOG_EVENT,catalog);return()=>window.removeEventListener(MODEL_CATALOG_EVENT,catalog)},[]);
 // The lock follows the real send promise instead of a timer. This prevents both a
 // dead button after failed first-chat creation and a premature second submit while
 // a valid creation/stream opening is still in progress.
 const submit=async()=>{if(!trimmed||tooLong||offline||sending||noModelsAvailable||submitGate.current)return;submitGate.current=true;setSlow(false);try{await Promise.resolve(onSend())}finally{submitGate.current=false}};
 const stop=()=>{setSlow(false);const key=pendingStreamIdempotencyKey(conversationId);if(conversationId&&key){void api(`/conversations/${conversationId}/messages/cancel/`,{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({idempotency_key:key})}).catch(()=>undefined)}onStop()};
 const key=(event:KeyboardEvent<HTMLTextAreaElement>)=>{if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();void submit()}};
 const changeModel=(next:string)=>{if(explicitModelControl){onModelChange?.(next);return;}setControlValue(next)};
 const blockedTitle=noModelsAvailable?"Сейчас нет доступных AI-моделей. Система перепроверит подключения автоматически.":offline?"Отправка станет доступна после восстановления сети":tooLong?"Сообщение превышает лимит 100 000 символов":selectedModelUnavailable?"Выбранная модель недоступна — запрос автоматически пойдёт на резервную модель":"Отправить · Enter";
 return <><div className="composerZone">{selectedModelUnavailable&&<div className="modelUnavailableNotice" role="status"><Icon name="warning" size={15}/><span>Выбранная модель сейчас недоступна. Можно отправлять сообщение — система автоматически использует рабочую резервную модель, не меняя ваш выбор.</span></div>}{noModelsAvailable&&<div className="modelUnavailableNotice" role="status"><Icon name="warning" size={15}/><span>AI временно недоступен. Проверяем подключения автоматически — отправка включится после восстановления рабочей модели.</span></div>}{catalogState==="error"&&!noModelsAvailable&&<div className="modelUnavailableNotice" role="status"><Icon name="warning" size={15}/><span>Каталог моделей временно не обновился. Чат остаётся доступен — сервер проверит рабочую модель при отправке.</span></div>}<div className={`composerShell ${focused?"focused":""} ${tooLong?"invalid":""}`}><textarea ref={ref} value={value} maxLength={MAX_MESSAGE_CHARS+5000} onChange={e=>setValue(e.target.value)} onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} onKeyDown={key} placeholder="Опишите задачу, задайте вопрос или добавьте материалы…" rows={3} aria-label="Сообщение" aria-invalid={tooLong}/><div className="composerBar"><div className="composerLeft"><button className="composerIcon" type="button" onClick={onOpenTools} aria-label="Добавить файл или инструмент" title="Файл, поиск и инструменты"><Icon name="plus"/></button><button className="composerIcon composerImageAction" type="button" onClick={()=>setImageStudioOpen(true)} aria-label={sourceImageId?"Создать или изменить изображение":"Создать изображение"} title={sourceImageId?"Создать новое или изменить приложенное изображение через OpenAI":"Создать изображение через OpenAI"}><Icon name="spark"/></button>{onAttachImage&&<button className="composerIcon composerVisionAction" type="button" onClick={onAttachImage} aria-label="Добавить изображение для анализа" title="Загрузить изображение и проанализировать"><Icon name="folderPlus"/></button>}{explicitModelControl&&<ModelPicker value={effectiveControlValue} models={controlModels} disabled={sending} onChange={changeModel}/>} {offline&&<span className="offlineChip">Нет сети · черновик сохранён</span>}{sending&&slow&&<span className="offlineChip">Ответ занимает больше времени, чем обычно</span>}{nearLimit&&<span className={`charCounter ${tooLong?"error":""}`}>{value.length.toLocaleString("ru-RU")} / {MAX_MESSAGE_CHARS.toLocaleString("ru-RU")}</span>}</div><div className="composerRight">{sending?<button className="sendButton stop" type="button" onClick={stop} aria-label="Остановить ответ" title="Остановить ответ"><Icon name="stop"/></button>:<button className="sendButton" type="button" disabled={!trimmed||offline||tooLong||noModelsAvailable} onClick={()=>void submit()} aria-label="Отправить" title={blockedTitle}><Icon name="send"/></button>}</div></div></div>{tooLong&&<div className="composerValidation" role="alert">Сократите сообщение до 100 000 символов. Текст сохранён как черновик.</div>}<div className="composerHint">Enter — отправить · Shift+Enter — новая строка · поиск и подбор модели работают автоматически</div></div><ChatImageStudio open={imageStudioOpen} onClose={()=>setImageStudioOpen(false)} conversationId={conversationId} ensureConversation={ensureConversation} sourceImageId={sourceImageId}/></>;
}
