"use client";

import {useMemo,useState} from "react";
import {api} from "../../../../lib/api";

type Node={id:string;title:string;type:string};
type Edge={from:string;to:string};
type Graph={version?:number;nodes?:Node[];edges?:Edge[]};
type Version={id:string;version:number;created_at:string;created_by:string;summary:{name?:string;role?:string;autonomy?:string;system_level?:string}};
type AgentLike={id:string;graph:Graph};
type Props={agent:AgentLike;onChange:(next:AgentLike)=>void};

const nodeTypes=[
 ["llm","AI думает / пишет"],["web","Поиск в интернете"],["research","Исследование"],["files","Работа с файлами"],
 ["image","Создать изображение"],["review","Проверка качества"],["approval","Спросить подтверждение"],["publish","Публикация"],
 ["analytics","Аналитика"],["github_read","Чтение GitHub"],["github_write","Изменение GitHub"],["code","Написать код"],
 ["sandbox","Запустить проверку"],["handoff","Передать другому агенту"],["wait","Подождать"],["finish","Завершить"],
] as const;

const rebuild=(nodes:Node[]):Graph=>({version:1,nodes,edges:nodes.slice(0,-1).map((node,index)=>({from:node.id,to:nodes[index+1].id}))});
const uid=()=>`step-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`;

export default function AgentGraphEditor({agent,onChange}:Props){
 const[nodes,setNodes]=useState<Node[]>(()=>agent.graph?.nodes?.map(n=>({...n}))||[]);
 const[versions,setVersions]=useState<Version[]>([]);
 const[busy,setBusy]=useState("");
 const[error,setError]=useState("");
 const[notice,setNotice]=useState("");
 const[historyOpen,setHistoryOpen]=useState(false);
 const labels=useMemo(()=>Object.fromEntries(nodeTypes),[]);

 const setNode=(index:number,patch:Partial<Node>)=>setNodes(current=>current.map((node,i)=>i===index?{...node,...patch}:node));
 const move=(index:number,delta:number)=>setNodes(current=>{const target=index+delta;if(target<0||target>=current.length)return current;const next=[...current];[next[index],next[target]]=[next[target],next[index]];return next;});
 const remove=(index:number)=>setNodes(current=>current.filter((_,i)=>i!==index));
 const add=()=>setNodes(current=>[...current,{id:uid(),title:"Новый шаг",type:"llm"}]);

 const save=async()=>{setBusy("save");setError("");setNotice("");try{const next=await api<AgentLike>(`/agents/${agent.id}/config/`,{method:"PATCH",body:JSON.stringify({graph:rebuild(nodes)})});onChange(next);setNodes(next.graph?.nodes||[]);setNotice("Карта сохранена. Предыдущая версия сохранена в истории.");}catch(e){setError(e instanceof Error?e.message:"Не удалось сохранить карту");}finally{setBusy("")}};
 const loadVersions=async()=>{setBusy("history");setError("");try{const rows=await api<Version[]>(`/agents/${agent.id}/versions/`);setVersions(rows);setHistoryOpen(true);}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить историю");}finally{setBusy("")}};
 const restore=async(version:Version)=>{if(!window.confirm(`Восстановить версию ${version.version}? Текущая конфигурация тоже сохранится в истории.`))return;setBusy(`restore:${version.id}`);setError("");try{const next=await api<AgentLike>(`/agents/${agent.id}/versions/${version.id}/restore/`,{method:"POST"});onChange(next);setNodes(next.graph?.nodes||[]);setNotice(`Версия ${version.version} восстановлена.`);setHistoryOpen(false);}catch(e){setError(e instanceof Error?e.message:"Не удалось восстановить версию");}finally{setBusy("")}};

 return <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"flex-start",marginBottom:14}}><div><h2 style={{margin:"0 0 5px"}}>Карта действий</h2><p style={{margin:0,opacity:.65}}>Соберите процесс сверху вниз. Агент будет видеть эту карту как разрешённый рабочий сценарий.</p></div><div style={{display:"flex",gap:8,flexWrap:"wrap"}}><button onClick={()=>void loadVersions()} disabled={!!busy} style={{padding:"8px 10px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>История</button><button onClick={()=>void save()} disabled={!!busy||!nodes.length} style={{padding:"8px 12px",border:0,borderRadius:9,fontWeight:700}}>{busy==="save"?"Сохраняем…":"Сохранить карту"}</button></div></div>
  {notice&&<div style={{padding:10,border:"1px solid #8ab492",borderRadius:10,marginBottom:12}}>{notice}</div>}{error&&<div style={{padding:10,border:"1px solid #ce747b",borderRadius:10,marginBottom:12}}>{error}</div>}
  <div style={{display:"grid",gap:9}}>{nodes.map((node,index)=><div key={node.id} style={{display:"grid",gridTemplateColumns:"34px minmax(0,1fr) 190px auto",gap:8,alignItems:"center",border:"1px solid #ddd",borderRadius:13,padding:9}}><div style={{width:30,height:30,borderRadius:999,border:"1px solid #bbb",display:"grid",placeItems:"center",fontSize:12}}>{index+1}</div><input value={node.title} maxLength={240} onChange={e=>setNode(index,{title:e.target.value})} style={{minWidth:0,padding:9,border:"1px solid #ccc",borderRadius:8}}/><select value={node.type} onChange={e=>setNode(index,{type:e.target.value})} style={{padding:9,border:"1px solid #ccc",borderRadius:8}}>{nodeTypes.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select><div style={{display:"flex",gap:4}}><button aria-label="Выше" disabled={index===0} onClick={()=>move(index,-1)}>↑</button><button aria-label="Ниже" disabled={index===nodes.length-1} onClick={()=>move(index,1)}>↓</button><button aria-label="Удалить" onClick={()=>remove(index)}>×</button></div></div>)}</div>
  {!nodes.length&&<div style={{padding:22,border:"1px dashed #bbb",borderRadius:13,textAlign:"center",opacity:.7}}>Карта пустая. Добавьте первый шаг.</div>}
  <button onClick={add} style={{marginTop:11,padding:"9px 12px",border:"1px dashed #aaa",borderRadius:10,background:"transparent"}}>+ Добавить шаг</button>
  {historyOpen&&<div style={{marginTop:16,borderTop:"1px solid #eee",paddingTop:14}}><div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}><strong>История версий</strong><button onClick={()=>setHistoryOpen(false)}>Закрыть</button></div>{versions.length?<div style={{display:"grid",gap:7,marginTop:10}}>{versions.map(version=><div key={version.id} style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",padding:"9px 0",borderBottom:"1px solid #eee"}}><div><strong>Версия {version.version}</strong><div style={{fontSize:12,opacity:.6}}>{new Date(version.created_at).toLocaleString("ru-RU")} · {version.summary?.name||"Агент"}</div></div><button disabled={!!busy} onClick={()=>void restore(version)}>{busy===`restore:${version.id}`?"Восстанавливаем…":"Восстановить"}</button></div>)}</div>:<p style={{opacity:.65}}>Сохранённых версий пока нет.</p>}</div>}
  <div style={{marginTop:12,fontSize:12,opacity:.55}}>Типы блоков: {nodes.map(n=>labels[n.type]||n.type).join(" → ")}</div>
 </article>;
}
