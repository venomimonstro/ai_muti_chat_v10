"use client";

import {useMemo,useState} from "react";

type PublishStatus="draft"|"publish";
type Node={id:string;title:string;type:string;status?:PublishStatus;prompt?:string;post_title?:string;slug?:string};
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
const allowedTypes=new Set(types.map(([value])=>value));

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
 const initial=useMemo(()=>((graph.nodes||[]) as Node[]).map(item=>({...item,status:item.type==="publish"?(item.status||"draft"):item.status})),[graph]);
 const[nodes,setNodes]=useState<Node[]>(initial);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const update=(index:number,patch:Partial<Node>)=>setNodes(current=>current.map((item,i)=>i===index?{...item,...patch}:item));
 const changeType=(index:number,type:string)=>setNodes(current=>current.map((item,i)=>i===index?{...item,type,status:type==="publish"?(item.status||"draft"):undefined}:item));
 const move=(index:number,delta:number)=>setNodes(current=>{const target=index+delta;if(target<0||target>=current.length)return current;const next=[...current];[next[index],next[target]]=[next[target],next[index]];return next});
 const remove=(index:number)=>setNodes(current=>current.filter((_,i)=>i!==index));
 const add=()=>setNodes(current=>[...current,{id:safeId("Новый шаг",current.length),title:"Новый шаг",type:"llm"}]);
 const save=async()=>{setBusy(true);setError("");try{const cleaned=nodes.map((node,index)=>{const type=allowedTypes.has(node.type as typeof types[number][0])?node.type:"llm";const next:Node={...node,id:node.id||safeId(node.title,index),title:node.title.trim()||`Шаг ${index+1}`,type};if(type==="publish")next.status=node.status==="publish"?"publish":"draft";else delete next.status;return next});await onSave(normalize(cleaned,Number(graph.version||1)+1));}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось сохранить карту")}finally{setBusy(false)}};
 return <div>
  <div style={{padding:"10px 12px",border:"1px solid #e2e2e2",borderRadius:11,marginBottom:12,fontSize:13,opacity:.72}}>Здесь доступны только шаги, которые Agent Studio действительно умеет выполнить. GitHub, код и sandbox настраиваются отдельно в Dev Studio.</div>
  <div style={{display:"grid",gap:10}}>{nodes.map((node,index)=><div key={node.id} style={{border:"1px solid #ddd",borderRadius:13,padding:10}}>
   <div style={{display:"grid",gridTemplateColumns:"36px minmax(0,1fr) 180px auto",gap:8,alignItems:"center"}}>
    <div style={{width:30,height:30,borderRadius:999,border:"1px solid #bbb",display:"grid",placeItems:"center",fontWeight:700}}>{index+1}</div>
    <input aria-label={`Название шага ${index+1}`} value={node.title} onChange={event=>update(index,{title:event.target.value})} disabled={disabled||busy} style={{minWidth:0,padding:9,border:"1px solid #ccc",borderRadius:9}}/>
    <select aria-label={`Тип шага ${index+1}`} value={allowedTypes.has(node.type as typeof types[number][0])?node.type:"llm"} onChange={event=>changeType(index,event.target.value)} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}>{types.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
    <div style={{display:"flex",gap:5}}><button type="button" onClick={()=>move(index,-1)} disabled={disabled||busy||index===0} aria-label="Поднять шаг">↑</button><button type="button" onClick={()=>move(index,1)} disabled={disabled||busy||index===nodes.length-1} aria-label="Опустить шаг">↓</button><button type="button" onClick={()=>remove(index)} disabled={disabled||busy||nodes.length<=1} aria-label="Удалить шаг">×</button></div>
   </div>
   {node.type==="publish"&&<div style={{display:"grid",gridTemplateColumns:"minmax(180px,.8fr) minmax(220px,1.2fr)",gap:10,margin:"10px 0 0 44px"}}><label style={{display:"grid",gap:5,fontSize:13}}>Что сделать в WordPress<select value={node.status||"draft"} onChange={event=>update(index,{status:event.target.value as PublishStatus})} disabled={disabled||busy} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="draft">Сохранить как черновик</option><option value="publish">Опубликовать сразу</option></select></label><div style={{fontSize:12,opacity:.65,alignSelf:"end",paddingBottom:8}}>{(node.status||"draft")==="publish"?"Материал станет публичным после выполнения policy/подтверждения.":"Безопасный режим: материал появится в WordPress как черновик."}</div></div>}
  </div>)}</div>
  {error&&<div style={{marginTop:10,color:"#b33140"}}>{error}</div>}
  <div style={{display:"flex",justifyContent:"space-between",gap:10,marginTop:12}}><button type="button" onClick={add} disabled={disabled||busy} style={{padding:"9px 12px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>+ Добавить шаг</button><button type="button" onClick={()=>void save()} disabled={disabled||busy||!nodes.length} style={{padding:"9px 13px",border:0,borderRadius:9,fontWeight:700}}>{busy?"Сохраняем…":"Сохранить карту"}</button></div>
 </div>;
}
