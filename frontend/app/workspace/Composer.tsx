"use client";

import {KeyboardEvent,useEffect,useRef,useState} from "react";
import {Icon} from "./Icons";

const MAX_MESSAGE_CHARS=100000;

export function Composer({value,setValue,sending,offline,onSend,onStop,onOpenTools}:{value:string;setValue:(value:string)=>void;sending:boolean;offline:boolean;onSend:()=>void;onStop:()=>void;onOpenTools:()=>void}){
 const ref=useRef<HTMLTextAreaElement|null>(null);const[focused,setFocused]=useState(false);const[slow,setSlow]=useState(false);const submitGate=useRef(false);const gateTimer=useRef<number|null>(null);
 const trimmed=value.trim();const tooLong=value.length>MAX_MESSAGE_CHARS;const nearLimit=value.length>90000;
 useEffect(()=>{const el=ref.current;if(!el)return;el.style.height="0px";el.style.height=`${Math.min(Math.max(el.scrollHeight,48),240)}px`;},[value]);
 useEffect(()=>{if(!sending){setSlow(false);return;}submitGate.current=false;if(gateTimer.current!==null){window.clearTimeout(gateTimer.current);gateTimer.current=null}const timer=window.setTimeout(()=>setSlow(true),30000);return()=>window.clearTimeout(timer)},[sending]);
 useEffect(()=>{if(!trimmed){submitGate.current=false;if(gateTimer.current!==null){window.clearTimeout(gateTimer.current);gateTimer.current=null}}},[trimmed]);
 useEffect(()=>()=>{if(gateTimer.current!==null)window.clearTimeout(gateTimer.current)},[]);
 const submit=()=>{if(!trimmed||tooLong||offline||sending||submitGate.current)return;submitGate.current=true;setSlow(false);onSend();gateTimer.current=window.setTimeout(()=>{submitGate.current=false;gateTimer.current=null},10000)};
 const stop=()=>{setSlow(false);onStop()};
 const key=(event:KeyboardEvent<HTMLTextAreaElement>)=>{if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();submit();}};
 return <div className="composerZone"><div className={`composerShell ${focused?"focused":""} ${tooLong?"invalid":""}`}>
   <textarea ref={ref} value={value} maxLength={MAX_MESSAGE_CHARS+5000} onChange={e=>setValue(e.target.value)} onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} onKeyDown={key} placeholder="Напишите сообщение…" rows={1} aria-label="Сообщение" aria-invalid={tooLong}/>
   <div className="composerBar"><div className="composerLeft"><button className="composerIcon" type="button" onClick={onOpenTools} aria-label="Добавить файл или инструмент" title="Файл, поиск и инструменты"><Icon name="plus"/></button>{!offline&&!sending&&<span className="composerEconomy" title="Списание происходит по фактически подтверждённому использованию">Оплата по факту</span>}{offline&&<span className="offlineChip">Нет сети · черновик сохранён</span>}{sending&&slow&&<span className="offlineChip">Ответ занимает больше времени, чем обычно</span>}{nearLimit&&<span className={`charCounter ${tooLong?"error":""}`}>{value.length.toLocaleString("ru-RU")} / {MAX_MESSAGE_CHARS.toLocaleString("ru-RU")}</span>}</div><div className="composerRight"><div className="composerTierSlot"/>{sending?<button className="sendButton stop" type="button" onClick={stop} aria-label="Остановить ответ" title="Остановить ответ"><Icon name="stop"/></button>:<button className="sendButton" type="button" disabled={!trimmed||offline||tooLong} onClick={submit} aria-label="Отправить" title={offline?"Отправка станет доступна после восстановления сети":tooLong?"Сообщение превышает лимит 100 000 символов":"Отправить · Enter"}><Icon name="send"/></button>}</div></div>
  </div>{tooLong&&<div className="composerValidation" role="alert">Сократите сообщение до 100 000 символов. Текст сохранён как черновик.</div>}<div className="composerHint">Модели могут ошибаться · списывается только подтверждённое использование</div></div>;
}
