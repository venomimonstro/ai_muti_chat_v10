"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type MemoryItem={id:string;scope:string;memory_type:string;content:string;pinned:boolean;enabled:boolean;importance_score:string;updated_at:string};
type MemoryPayload={enabled:boolean;project_enabled:boolean;project:string|null;items:MemoryItem[]};
type Props={agentId:string};

const typeLabel:Record<string,string>={fact:"Факт",preference:"Предпочтение",instruction:"Инструкция",decision:"Решение"};

export default function AgentMemoryPanel({agentId}:Props){
 const[data,setData]=useState<MemoryPayload|null>(null);const[text,setText]=useState("");const[type,setType]=useState("fact");const[scope,setScope]=useState("auto");const[busy,setBusy]=useState("");const[error,setError]=useState("");
 const load=async()=>{try{setData(await api<MemoryPayload>(`/agents/${agentId}/memory/`));setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить память")}};
 useEffect(()=>{void load()},[agentId]);
 const add=async()=>{const content=text.trim();if(!content)return;setBusy("add");setError("");try{await api(`/agents/${agentId}/memory/`,{method:"POST",body:JSON.stringify({content,memory_type:type,scope:scope==="auto"?"":scope,pinned:true})});setText("");await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось добавить память")}finally{setBusy("")}};
 const togglePin=async(item:MemoryItem)=>{setBusy(item.id);setError("");try{await api(`/agents/${agentId}/memory/${item.id}/`,{method:"PATCH",body:JSON.stringify({pinned:!item.pinned})});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось изменить запись")}finally{setBusy("")}};
 const edit=async(item:MemoryItem)=>{const content=window.prompt("Что должен помнить агент?",item.content);if(content===null||!content.trim()||content.trim()===item.content)return;setBusy(item.id);setError("");try{await api(`/agents/${agentId}/memory/${item.id}/`,{method:"PATCH",body:JSON.stringify({content:content.trim()})});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось изменить память")}finally{setBusy("")}};
 const remove=async(item:MemoryItem)=>{if(!window.confirm("Удалить эту запись из памяти?"))return;setBusy(item.id);setError("");try{await api(`/agents/${agentId}/memory/${item.id}/`,{method:"DELETE"});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось удалить память")}finally{setBusy("")}};
 return <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}>
  <h3 style={{marginTop:0}}>Что знает сотрудник</h3><p style={{fontSize:13,opacity:.62,marginTop:-4}}>Это реальные записи памяти, которые агент использует как контекст. Их можно исправить или удалить.</p>
  {error&&<div style={{padding:9,border:"1px solid #ce747b",borderRadius:9,marginBottom:10}}>{error}</div>}
  <textarea rows={3} value={text} onChange={e=>setText(e.target.value)} placeholder="Например: Компания продаёт мебель в Москве. В текстах обращаться на «вы»." style={{width:"100%",boxSizing:"border-box",padding:10,border:"1px solid #ccc",borderRadius:9,resize:"vertical"}}/>
  <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginTop:8}}><select value={type} onChange={e=>setType(e.target.value)} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="fact">Факт</option><option value="preference">Предпочтение</option><option value="instruction">Инструкция</option><option value="decision">Решение</option></select><select value={scope} onChange={e=>setScope(e.target.value)} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="auto">Автоматически</option><option value="global">Для всех моих агентов</option>{data?.project&&<option value="project">Только для этого проекта</option>}</select></div>
  <button disabled={busy==="add"||!text.trim()} onClick={()=>void add()} style={{marginTop:9,padding:"9px 12px",border:0,borderRadius:9,fontWeight:700}}>{busy==="add"?"Добавляем…":"+ Запомнить"}</button>
  <div style={{marginTop:14,borderTop:"1px solid #eee",paddingTop:8}}>{!data?.items?.length?<p style={{opacity:.6}}>Подходящих записей памяти пока нет.</p>:data.items.map(item=><div key={item.id} style={{padding:"10px 0",borderBottom:"1px solid #eee"}}><div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start"}}><div><div style={{display:"flex",gap:6,alignItems:"center",flexWrap:"wrap"}}><span style={{fontSize:11,border:"1px solid #ddd",borderRadius:999,padding:"3px 6px"}}>{typeLabel[item.memory_type]||item.memory_type}</span><span style={{fontSize:11,opacity:.55}}>{item.scope==="project"?"проект":"общая"}{item.pinned?" · закреплено":""}</span></div><div style={{marginTop:6,lineHeight:1.45}}>{item.content}</div></div><div style={{display:"flex",gap:4,flexShrink:0}}><button disabled={!!busy} onClick={()=>void togglePin(item)} title={item.pinned?"Открепить":"Закрепить"}>{item.pinned?"★":"☆"}</button><button disabled={!!busy} onClick={()=>void edit(item)}>Изм.</button><button disabled={!!busy} onClick={()=>void remove(item)}>×</button></div></div></div>)}</div>
 </article>;
}
