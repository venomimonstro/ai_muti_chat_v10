"use client";

import {useMemo,useState} from "react";

type Node={id:string;title:string;type:string};
type Graph={version?:number;nodes?:Node[];edges?:Array<{from:string;to:string}>};

type Props={graph:Graph;disabled?:boolean;onSave:(graph:Graph)=>Promise<void>};

const types=[
 ["llm","AI-задача"],
 ["research","Исследование"],
 ["web","Интернет"],
 ["files","Файлы проекта"],
 ["image","Изображение"],
 ["review","Проверка"],
 ["analytics","Аналитика"],
 ["approval","Подтверждение пользователя"],
 ["publish","Публикация"],
] as const;

const normalize=(nodes:Node[],version=1):Graph=>({
 version,
 nodes,
 edges:nodes.slice(0,-1).map((node,index)=>({from:node.id,to:nodes[index+1].id})),
});

const safeId=(title:string,index:number)=>{
 const base=title.toLowerCase().replace(/[^a-zа-яё0-9]+/gi,"-").replace(/^-|-$/g,"").slice(0,48);
 return `${base||"step"}-${Date.now().toString(36)}-${index}`;
};

export default function AgentGraphEditor({graph,disabled,onSave}:Props){
 const initial=useMemo(()=>((graph.nodes||[]) as Node[]).map(item=>({...item})),[graph]);
 const[nodes,setNodes]=useState<Node[]>(initial);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const update=(index:number,patch:Partial<Node>)=>setNodes(current=>current.map((item,i)=>i===index?{...item,...patch}:item));
 const move=(index:number,delta:number)=>setNodes(current=>{const target=index+delta;if(target<0||target>=current.length)return current;const next=[...current];[next[index],next[target]]=[next[target],next[index]];return next});
 const remove=(index:number)=>setNodes(current=>current.filter((_,i)=>i!==index));
 const add=()=>setNodes(current=>[...current,{id:safeId("Новый шаг",current.length),title:"Новый шаг",type:"llm"}]);
 const save=async()=>{setBusy(true);setError("");try{const cleaned=nodes.map((node,index)=>({id:node.id||safeId(node.title,index),title:node.title.trim()||`Шаг ${index+1}`,type:node.type||"llm"}));await onSave(normalize(cleaned,Number(graph.version||1)+1));}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось сохранить карту")}finally{setBusy(false)}};
 return <div>
  <div style={{padding:"10px 12px",border:"1px solid #e2e2e2",borderRadius:11,marginBottom:12,fontSize:13,opacity:.72}}>Здесь доступны только шаги, которые Agent Studio действительно умеет выполнить. GitHub, код и sandbox настраиваются отдельно в Dev Studio.</div>
  <div style={{display:"grid",gap:10}}>{nodes.map((node,index)=><div key={node.id} style={{display:"grid",gridTemplateColumns:"36px minmax(0,1fr) 180px auto",gap:8,alignItems:"center",border:"1px solid #ddd",borderRadius:13,padding:10}}>
   <div style={{width:30,height:30,borderRadius:999,border:"1px solid #bbb",display:"grid",placeItems:"center",fontWeight:700}}>{index+1}</div>
   <input aria-label={`Название шага ${index+1}`} value={node.title} onChange={event=>update(index,{title:event.target.value})} disabled={disabled||busy} style={{minWidth:0,padding:9,border:"1px solid #ccc",borderRadius:9}}/>
   <select aria-label={`Тип шага ${index+1}`} value={types.some(([value])=>value===node.type)?node.type:"llm"} onChange={event=>update(index,{type:event.target.value})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}>{types.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
   <div style={{display:"flex",gap:5}}><button type="button" onClick={()=>move(index,-1)} disabled={disabled||busy||index===0} aria-label="Поднять шаг">↑</button><button type="button" onClick={()=>move(index,1)} disabled={disabled||busy||index===nodes.length-1} aria-label="Опустить шаг">↓</button><button type="button" onClick={()=>remove(index)} disabled={disabled||busy||nodes.length<=1} aria-label="Удалить шаг">×</button></div>
  </div>)}</div>
  {error&&<div style={{marginTop:10,color:"#b33140"}}>{error}</div>}
  <div style={{display:"flex",justifyContent:"space-between",gap:10,marginTop:12}}><button type="button" onClick={add} disabled={disabled||busy} style={{padding:"9px 12px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>+ Добавить шаг</button><button type="button" onClick={()=>void save()} disabled={disabled||busy||!nodes.length} style={{padding:"9px 13px",border:0,borderRadius:9,fontWeight:700}}>{busy?"Сохраняем…":"Сохранить карту"}</button></div>
 </div>;
}
