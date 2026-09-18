"use client";

import {KeyboardEvent} from "react";

export function Composer({value,setValue,sending,onSend,onStop}:{value:string;setValue:(value:string)=>void;sending:boolean;onSend:()=>void;onStop:()=>void}){
  const key=(event:KeyboardEvent<HTMLTextAreaElement>)=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();onSend();}};
  return <div className="proComposerZone"><div className={`proComposer ${sending?"busy":""}`}><textarea value={value} onChange={e=>setValue(e.target.value)} onKeyDown={key} placeholder="Напишите сообщение…" rows={1} aria-label="Сообщение"/><div><span>Enter — отправить · Shift+Enter — новая строка</span>{sending?<button className="stop" onClick={onStop}>■</button>:<button disabled={!value.trim()} onClick={onSend}>↑</button>}</div></div></div>;
}
