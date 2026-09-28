"use client";

import {KeyboardEvent,useEffect,useRef,useState} from "react";
import type {AIModel} from "../../lib/types";
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
};

function existingModelControl(){
 if(typeof document==="undefined")return null;
 return document.querySelector<HTMLSelectElement>(".headerControls .selectControl:first-child select");
}

function readExistingModels():AIModel[]{
 const select=existingModelControl();if(!select)return [];
 return Array.from(select.options).filter(option=>option.value.startsWith("model:")).map(option=>({slug:option.value.slice(6),display_name:(option.textContent??option.value).replace(/ · недоступна$/,""),provider:(option.textContent??"").toLowerCase().includes("системная")?"gigachat":"Модели",model_version:null,exact_api_id:"",capabilities:[],context_window:0,max_output_tokens:0,available:!option.disabled,health_state:option.disabled?"unavailable":"healthy",price:null}));
}

export function Composer({value,setValue,sending,offline,onSend,onStop,onOpenTools,modelValue,models,onModelChange}:ComposerProps){
 const ref=useRef<HTMLTextAreaElement|null>(null);const[focused,setFocused]=useState(false);const[slow,setSlow]=useState(false);const[controlValue,setControlValue]=useState("auto:balanced");const[controlModels,setControlModels]=useState<AIModel[]>(models??[]);const submitGate=useRef(false);const gateTimer=useRef<number|null>(null);
 const trimmed=value.trim();const tooLong=value.length>MAX_MESSAGE_CHARS;const nearLimit=value.length>90000;
 useEffect(()=>{const el=ref.current;if(!el)return;el.style.height="0px";el.style.height=`${Math.min(Math.max(el.scrollHeight,48),240)}px`;},[value]);
 useEffect(()=>{if(!sending){setSlow(false);return;}submitGate.current=false;if(gateTimer.current!==null){window.clearTimeout(gateTimer.current);gateTimer.current=null}const timer=window.setTimeout(()=>setSlow(true),30000);return()=>window.clearTimeout(timer)},[sending]);
 useEffect(()=>{if(!trimmed){submitGate.current=false;if(gateTimer.current!==null){window.clearTimeout(gateTimer.current);gateTimer.current=null}}},[trimmed]);
 useEffect(()=>()=>{if(gateTimer.current!==null)window.clearTimeout(gateTimer.current)},[]);
 useEffect(()=>{if(modelValue){setControlValue(modelValue);setControlModels(models??[]);return;}const select=existingModelControl();if(select)setControlValue(select.value||"auto:balanced");setControlModels(readExistingModels())},[modelValue,models]);
 const submit=()=>{if(!trimmed||tooLong||offline||sending||submitGate.current)return;submitGate.current=true;setSlow(false);onSend();gateTimer.current=window.setTimeout(()=>{submitGate.current=false;gateTimer.current=null},10000)};
 const stop=()=>{setSlow(false);onStop()};
 const key=(event:KeyboardEvent<HTMLTextAreaElement>)=>{if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();submit();}};
 const changeModel=(next:string)=>{setControlValue(next);if(onModelChange){onModelChange(next);return;}const select=existingModelControl();if(!select)return;select.value=next;select.dispatchEvent(new Event("change",{bubbles:true}))};
 return <div className="composerZone"><div className={`composerShell ${focused?"focused":""} ${tooLong?"invalid":""}`}>
   <textarea ref={ref} value={value} maxLength={MAX_MESSAGE_CHARS+5000} onChange={e=>setValue(e.target.value)} onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} onKeyDown={key} placeholder="Напишите сообщение…" rows={1} aria-label="Сообщение" aria-invalid={tooLong}/>
   <div className="composerBar"><div className="composerLeft"><button className="composerIcon" type="button" onClick={onOpenTools} aria-label="Добавить файл или инструмент" title="Файл, поиск и инструменты"><Icon name="plus"/></button><ModelPicker value={controlValue} models={controlModels} disabled={sending} onChange={changeModel}/>{offline&&<span className="offlineChip">Нет сети · черновик сохранён</span>}{sending&&slow&&<span className="offlineChip">Ответ занимает больше времени, чем обычно</span>}{nearLimit&&<span className={`charCounter ${tooLong?"error":""}`}>{value.length.toLocaleString("ru-RU")} / {MAX_MESSAGE_CHARS.toLocaleString("ru-RU")}</span>}</div><div className="composerRight">{sending?<button className="sendButton stop" type="button" onClick={stop} aria-label="Остановить ответ" title="Остановить ответ"><Icon name="stop"/></button>:<button className="sendButton" type="button" disabled={!trimmed||offline||tooLong} onClick={submit} aria-label="Отправить" title={offline?"Отправка станет доступна после восстановления сети":tooLong?"Сообщение превышает лимит 100 000 символов":"Отправить · Enter"}><Icon name="send"/></button>}</div></div>
  </div>{tooLong&&<div className="composerValidation" role="alert">Сократите сообщение до 100 000 символов. Текст сохранён как черновик.</div>}<div className="composerHint">Режим можно менять перед каждым запросом · списывается только подтверждённое использование</div></div>;
}
