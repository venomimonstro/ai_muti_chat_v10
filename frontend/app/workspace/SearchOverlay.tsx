"use client";

import {useEffect,useRef,useState} from "react";
import {api} from "../../lib/api";
import type {SearchResult} from "../../lib/types";
import {Icon} from "./Icons";

type SearchResponse={query:string;results:SearchResult[]};
export function SearchOverlay({open,onClose,onOpenConversation}:{open:boolean;onClose:()=>void;onOpenConversation:(id:string)=>void}){
 const[input,setInput]=useState("");const[items,setItems]=useState<SearchResult[]>([]);const[busy,setBusy]=useState(false);const[error,setError]=useState("");const ref=useRef<HTMLInputElement|null>(null);const requestRef=useRef(0);
 const dialogRef=useRef<HTMLDialogElement|null>(null);
 const normalized=input.trim();
 useEffect(()=>{const dialog=dialogRef.current;if(!open||!dialog)return;const previous=document.activeElement as HTMLElement|null;dialog.showModal();ref.current?.focus();return()=>{dialog.close();previous?.focus()}},[open]);
 useEffect(()=>{
  const requestId=++requestRef.current;
  if(!open||normalized.length<2){setItems([]);setBusy(false);setError("");return;}
  const controller=new AbortController();setBusy(true);setItems([]);setError("");
  const timer=window.setTimeout(async()=>{try{const response=await api<SearchResponse>(`/search/?q=${encodeURIComponent(normalized)}&limit=30`,{signal:controller.signal});if(requestRef.current===requestId)setItems(response.results)}catch(reason){if(requestRef.current===requestId&&!controller.signal.aborted)setError(reason instanceof Error?reason.message:"Не удалось выполнить поиск")}finally{if(requestRef.current===requestId)setBusy(false)}},250);
  return()=>{++requestRef.current;controller.abort();window.clearTimeout(timer)};
 },[normalized,open]);
 const openResult=(item:SearchResult)=>{const conversationId=item.navigation?.conversation_id||item.conversation_id;if(conversationId){onOpenConversation(conversationId);onClose();return;}const projectId=item.navigation?.project_id||item.project_id;if(projectId){window.location.assign(`/app/projects?project=${encodeURIComponent(projectId)}`);return;}if(item.type==="file"){window.location.assign("/app/projects");return;}};
 if(!open)return null;
 return <dialog ref={dialogRef} className="searchOverlay searchDialog" onCancel={event=>{event.preventDefault();onClose()}} aria-labelledby="workspace-search-title"><div className="searchBox"><Icon name="search"/><span id="workspace-search-title" className="srOnly">Поиск</span><input ref={ref} value={input} onChange={e=>setInput(e.target.value)} placeholder="Поиск по чатам, сообщениям, проектам и файлам" aria-label="Поиск по рабочему пространству" aria-controls="workspace-search-results"/><button className="iconButton" onClick={onClose} aria-label="Закрыть"><Icon name="x"/></button></div><div id="workspace-search-results" className="searchResults" aria-live="polite">{busy&&<p className="mutedState">Ищем…</p>}{error&&<p className="inlineError" role="alert">{error}</p>}{!busy&&!error&&normalized.length<2&&<p className="mutedState">Введите минимум 2 символа.</p>}{!busy&&normalized.length>=2&&items.length===0&&!error&&<p className="mutedState">Ничего не найдено.</p>}{items.map(item=><button key={`${item.type}:${item.id}`} className="searchResult" onClick={()=>openResult(item)}><span className="searchType">{item.type==="conversation"?"Чат":item.type==="message"?"Сообщение":item.type==="project"?"Проект":"Файл"}</span><b>{item.title}</b><span>{item.excerpt}</span></button>)}</div></dialog>;
}
