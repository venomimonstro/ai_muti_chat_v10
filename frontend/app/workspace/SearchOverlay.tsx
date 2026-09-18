"use client";

import {useEffect,useRef,useState} from "react";
import {api} from "../../lib/api";
import type {SearchResult} from "../../lib/types";
import {Icon} from "./Icons";

type SearchResponse={query:string;results:SearchResult[]};
export function SearchOverlay({open,onClose,onOpenConversation}:{open:boolean;onClose:()=>void;onOpenConversation:(id:string)=>void}){
 const[input,setInput]=useState("");const[items,setItems]=useState<SearchResult[]>([]);const[busy,setBusy]=useState(false);const[error,setError]=useState("");const ref=useRef<HTMLInputElement|null>(null);
 useEffect(()=>{if(open){setTimeout(()=>ref.current?.focus(),0);}},[open]);
 useEffect(()=>{if(!open||input.trim().length<2){setItems([]);return;}const timer=window.setTimeout(async()=>{setBusy(true);setError("");try{const response=await api<SearchResponse>(`/search/?q=${encodeURIComponent(input.trim())}&limit=30`);setItems(response.results);}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось выполнить поиск");}finally{setBusy(false);}},250);return()=>window.clearTimeout(timer);},[input,open]);
 useEffect(()=>{if(!open)return;const handler=(e:KeyboardEvent)=>{if(e.key==="Escape")onClose();};window.addEventListener("keydown",handler);return()=>window.removeEventListener("keydown",handler);},[open,onClose]);
 const openResult=(item:SearchResult)=>{const conversationId=item.navigation?.conversation_id||item.conversation_id;if(conversationId){onOpenConversation(conversationId);onClose();return;}const projectId=item.navigation?.project_id||item.project_id;if(projectId){window.location.assign(`/app/projects?project=${encodeURIComponent(projectId)}`);return;}if(item.type==="file"){window.location.assign("/app/projects");return;}};
 if(!open)return null;
 return <div className="overlayBackdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="searchOverlay" role="dialog" aria-modal="true" aria-label="Поиск"><div className="searchBox"><Icon name="search"/><input ref={ref} value={input} onChange={e=>setInput(e.target.value)} placeholder="Поиск по чатам, сообщениям, проектам и файлам"/><button className="iconButton" onClick={onClose} aria-label="Закрыть"><Icon name="x"/></button></div><div className="searchResults">{busy&&<p className="mutedState">Ищем…</p>}{error&&<p className="inlineError">{error}</p>}{!busy&&!error&&input.length<2&&<p className="mutedState">Введите минимум 2 символа.</p>}{!busy&&input.length>=2&&items.length===0&&!error&&<p className="mutedState">Ничего не найдено.</p>}{items.map(item=><button key={`${item.type}:${item.id}`} className="searchResult" onClick={()=>openResult(item)}><span className="searchType">{item.type==="conversation"?"Чат":item.type==="message"?"Сообщение":item.type==="project"?"Проект":"Файл"}</span><b>{item.title}</b><span>{item.excerpt}</span></button>)}</div></section></div>;
}
