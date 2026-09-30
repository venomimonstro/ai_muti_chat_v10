"use client";

import {KeyboardEvent,useEffect,useRef,useState} from "react";
import {api} from "../../lib/api";
import type {AIModel} from "../../lib/types";
import {ChatImageStudio} from "./ChatImageStudio";
import {Icon} from "./Icons";
import {ModelPicker} from "./ModelPicker";

const MAX_MESSAGE_CHARS=100000;

type ComposerProps={
 value:string;
 setValue:(value:string)=>void;
 sending:boolean;
 offline:boolean;
 onSend:()=>void;
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

function existingModelControl(){
 if(typeof document==="undefined")return null;
 return document.querySelector<HTMLSelectElement>(".headerControls .selectControl:first-child select");
}

function pendingStreamIdempotencyKey(conversationId:string|null|undefined){
 if(typeof window==="undefined"||!conversationId)return"";
 try{
  const raw=localStorage.getItem(`aiws:pending-stream:${conversationId}`);
  if(!raw)return"";
  const parsed=JSON.parse(raw) as {idempotencyKey?:unknown};
  return typeof parsed.idempotencyKey==="string"?parsed.idempotencyKey:"";
 }catch{return""}
}

export function Composer({value,setValue,sending,offline,onSend,onStop,onOpenTools,modelValue,models,onModelChange,conversationId,ensureConversation,onAttachImage,sourceImageId}:ComposerProps){
 const ref=useRef<HTMLTextAreaElement|null>(null);const[focused,setFocused]=useState(false);const[slow,setSlow]=useState(false);const[controlValue,setControlValue]=useState("auto:balanced");const[controlModels,setControlModels]=useState<AIModel[]>(models??[]);const[imageStudioOpen,setImageStudioOpen]=useState(false);const submitGate=useRef(false);const gateTimer=useRef<number|null>(null);
 const trimmed=value.trim();const tooLong=value.length>MAX_MESSAGE_CHARS;const nearLimit=value.length>90000;
 const selectedManualSlug=controlValue.startsWith("model:")?controlValue.slice(6):"";
 const selectedManualModel=selectedManualSlug?controlModels.find(item=>item.slug===selectedManualSlug):undefined;
 const selectedModelUnavailable=Boolean(selectedManualSlug&&(!selectedManualModel||!selectedManualModel.available));
 useEffect(()=>{const el=ref.current;if(!el)return;el.style.height="0px";el.style.height=`${Math.min(Math.max(el.scrollHeight,48),240)}px`;},[value]);
 useEffect(()=>{if(!sending){setSlow(false);return;}submitGate.current=false;if(gateTimer.current!==null){window.clearTimeout(gateTimer.current);gateTimer.current=null}const timer=window.setTimeout(()=>setSlow(true),30000);return()=>window.clearTimeout(timer)},[sending]);
 useEffect(()=>{if(!trimmed){submitGate.current=false;if(gateTimer.current!==null){window.clearTimeout(gateTimer.current);gateTimer.current=null}}},[trimmed]);
 useEffect(()=>()=>{if(gateTimer.current!==null)window.clearTimeout(gateTimer.current)},[]);
 useEffect(()=>{
  let cancelled=false;
  const hidden=existingModelControl();
  const syncValue=()=>{if(!cancelled)setControlValue(modelValue??hidden?.value??"auto:balanced")};
  syncValue();
  if(modelValue!==undefined||models!==undefined){
   setControlModels(models??[]);
  }else{
   void api<AIModel[]>("/models/").then(rows=>{if(!cancelled)setControlModels(rows)}).catch(()=>{if(!cancelled)setControlModels([])});
  }
  hidden?.addEventListener("change",syncValue);
  return()=>{cancelled=true;hidden?.removeEventListener("change",syncValue)};
 },[modelValue,models]);
 const submit=()=>{if(!trimmed||tooLong||offline||sending||selectedModelUnavailable||submitGate.current)return;submitGate.current=true;setSlow(false);onSend();gateTimer.current=window.setTimeout(()=>{submitGate.current=false;gateTimer.current=null},10000)};
 const stop=()=>{
  setSlow(false);
  const key=pendingStreamIdempotencyKey(conversationId);
  if(conversationId&&key){
   void api(`/conversations/${conversationId}/messages/cancel/`,{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({idempotency_key:key})}).catch(()=>undefined);
  }
  onStop();
 };
 const key=(event:KeyboardEvent<HTMLTextAreaElement>)=>{if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();submit();}};
 const changeModel=(next:string)=>{setControlValue(next);if(onModelChange){onModelChange(next);return;}const select=existingModelControl();if(!select)return;select.value=next;select.dispatchEvent(new Event("change",{bubbles:true}))};
 return <><div className="composerZone">{selectedModelUnavailable&&<div className="modelUnavailableNotice" role="alert"><Icon name="warning" size={15}/><span>Выбранная модель сейчас недоступна. Выберите AUTO или другую доступную модель.</span></div>}<div className={`composerShell ${focused?"focused":""} ${tooLong?"invalid":""}`}>
   <textarea ref={ref} value={value} maxLength={MAX_MESSAGE_CHARS+5000} onChange={e=>setValue(e.target.value)} onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} onKeyDown={key} placeholder="Напишите сообщение…" rows={1} aria-label="Сообщение" aria-invalid={tooLong}/>
   <div className="composerBar"><div className="composerLeft"><button className="composerIcon" type="button" onClick={onOpenTools} aria-label="Добавить файл или инструмент" title="Файл, поиск и инструменты"><Icon name="plus"/></button><button className="composerIcon composerImageAction" type="button" onClick={()=>setImageStudioOpen(true)} aria-label={sourceImageId?"Создать или изменить изображение":"Создать изображение"} title={sourceImageId?"Создать новое или изменить приложенное изображение через OpenAI":"Создать изображение через OpenAI"}><Icon name="spark"/></button>{onAttachImage&&<button className="composerIcon composerVisionAction" type="button" onClick={onAttachImage} aria-label="Добавить изображение для анализа" title="Загрузить изображение и проанализировать"><Icon name="folderPlus"/></button>}<ModelPicker value={controlValue} models={controlModels} disabled={sending} onChange={changeModel}/>{offline&&<span className="offlineChip">Нет сети · черновик сохранён</span>}{sending&&slow&&<span className="offlineChip">Ответ занимает больше времени, чем обычно</span>}{nearLimit&&<span className={`charCounter ${tooLong?"error":""}`}>{value.length.toLocaleString("ru-RU")} / {MAX_MESSAGE_CHARS.toLocaleString("ru-RU")}</span>}</div><div className="composerRight">{sending?<button className="sendButton stop" type="button" onClick={stop} aria-label="Остановить ответ" title="Остановить ответ"><Icon name="stop"/></button>:<button className="sendButton" type="button" disabled={!trimmed||offline||tooLong||selectedModelUnavailable} onClick={submit} aria-label="Отправить" title={selectedModelUnavailable?"Сначала выберите доступную модель":offline?"Отправка станет доступна после восстановления сети":tooLong?"Сообщение превышает лимит 100 000 символов":"Отправить · Enter"}><Icon name="send"/></button>}</div></div>
  </div>{tooLong&&<div className="composerValidation" role="alert">Сократите сообщение до 100 000 символов. Текст сохранён как черновик.</div>}<div className="composerHint">Можно прикреплять изображения для анализа, сравнивать и редактировать их или создавать новые через OpenAI · списывается только подтверждённое использование</div></div><ChatImageStudio open={imageStudioOpen} onClose={()=>setImageStudioOpen(false)} conversationId={conversationId} ensureConversation={ensureConversation} sourceImageId={sourceImageId}/></>;
}
