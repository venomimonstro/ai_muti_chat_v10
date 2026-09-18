"use client";

import {KeyboardEvent,useEffect,useRef,useState} from "react";
import {Icon} from "./Icons";

export function Composer({value,setValue,sending,offline,onSend,onStop,onOpenTools}:{value:string;setValue:(value:string)=>void;sending:boolean;offline:boolean;onSend:()=>void;onStop:()=>void;onOpenTools:()=>void}){
 const ref=useRef<HTMLTextAreaElement|null>(null);const[focused,setFocused]=useState(false);const[slow,setSlow]=useState(false);
 useEffect(()=>{const el=ref.current;if(!el)return;el.style.height="0px";el.style.height=`${Math.min(Math.max(el.scrollHeight,48),220)}px`;},[value]);
 useEffect(()=>{setSlow(false);if(!sending)return;const timer=window.setTimeout(()=>setSlow(true),30000);return()=>window.clearTimeout(timer)},[sending]);
 const key=(event:KeyboardEvent<HTMLTextAreaElement>)=>{if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();if(!sending&&value.trim())onSend();}};
 return <div className="composerZone"><div className={`composerShell ${focused?"focused":""}`}>
   <textarea ref={ref} value={value} onChange={e=>setValue(e.target.value)} onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} onKeyDown={key} placeholder="Спросите что-нибудь…" rows={1} aria-label="Сообщение"/>
   <div className="composerBar"><div className="composerLeft"><button className="composerIcon" type="button" onClick={onOpenTools} aria-label="Добавить файл или инструмент" title="Добавить файл или инструмент"><Icon name="plus"/></button>{offline&&<span className="offlineChip">Нет сети · черновик сохранён</span>}{slow&&<span className="offlineChip">Ответ занимает больше времени, чем обычно</span>}</div>{sending?<button className="sendButton stop" type="button" onClick={onStop} aria-label="Остановить ответ" title="Остановить ответ"><Icon name="stop"/></button>:<button className="sendButton" type="button" disabled={!value.trim()||offline} onClick={onSend} aria-label="Отправить" title={offline?"Отправка станет доступна после восстановления сети":"Отправить"}><Icon name="send"/></button>}</div>
  </div><div className="composerHint">AI может ошибаться. Важную информацию проверяйте.</div></div>;
}
