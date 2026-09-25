"use client";

import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";

type Schedule={id:string;agent:string|null;team:string|null;name:string;objective:string;enabled:boolean;interval_minutes:number;next_run_at:string;last_run_at:string|null;skip_if_running:boolean};
type Props={agentId:string;defaultObjective:string;active:boolean};

const presets=[
 [60,"Каждый час"],[360,"Каждые 6 часов"],[720,"Каждые 12 часов"],[1440,"Каждый день"],[10080,"Раз в неделю"],
] as const;

export default function AgentSchedulePanel({agentId,defaultObjective,active}:Props){
 const[items,setItems]=useState<Schedule[]>([]);const[busy,setBusy]=useState("");const[error,setError]=useState("");const[objective,setObjective]=useState(defaultObjective);const[interval,setIntervalValue]=useState(1440);
 const own=useMemo(()=>items.filter(item=>item.agent===agentId),[items,agentId]);
 const load=async()=>{try{const rows=await api<Schedule[]>("/agent-schedules/");setItems(rows);setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить расписание")}};
 useEffect(()=>{void load()},[agentId]);
 useEffect(()=>{setObjective(defaultObjective)},[defaultObjective]);
 const create=async()=>{if(!active){setError("Сначала активируйте агента");return}setBusy("create");setError("");try{await api("/agent-schedules/",{method:"POST",body:JSON.stringify({agent:agentId,team:null,name:"Автономный запуск",objective:objective.trim(),enabled:true,interval_minutes:interval,skip_if_running:true})});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось создать расписание")}finally{setBusy("")}};
 const toggle=async(item:Schedule)=>{setBusy(item.id);setError("");try{await api(`/agent-schedules/${item.id}/`,{method:"PATCH",body:JSON.stringify({enabled:!item.enabled})});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось изменить расписание")}finally{setBusy("")}};
 const remove=async(item:Schedule)=>{if(!window.confirm("Удалить это расписание?"))return;setBusy(item.id);setError("");try{await api(`/agent-schedules/${item.id}/`,{method:"DELETE"});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось удалить расписание")}finally{setBusy("")}};
 const formatDate=(value:string|null)=>value?new Date(value).toLocaleString("ru-RU"):"ещё не запускался";
 return <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}>
  <h3 style={{marginTop:0}}>Автономный режим</h3><p style={{opacity:.65,marginTop:-4}}>Запускайте сотрудника автоматически. Если предыдущий запуск ещё работает, новый будет пропущен.</p>
  {error&&<div style={{padding:9,border:"1px solid #ce747b",borderRadius:9,marginBottom:10}}>{error}</div>}
  <label style={{display:"grid",gap:5,marginBottom:9}}>Что делать при каждом запуске<textarea rows={3} value={objective} onChange={e=>setObjective(e.target.value)} placeholder="Например: подготовь один пост на актуальную тему" style={{padding:9,border:"1px solid #ccc",borderRadius:9,resize:"vertical"}}/></label>
  <label style={{display:"grid",gap:5}}>Как часто<select value={interval} onChange={e=>setIntervalValue(Number(e.target.value))} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}>{presets.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
  <button onClick={()=>void create()} disabled={!!busy||!active||!objective.trim()} style={{marginTop:10,padding:"9px 12px",border:0,borderRadius:9,fontWeight:700}}>{busy==="create"?"Создаём…":"+ Добавить расписание"}</button>
  {own.length>0&&<div style={{marginTop:14,borderTop:"1px solid #eee",paddingTop:10}}>{own.map(item=><div key={item.id} style={{padding:"9px 0",borderBottom:"1px solid #eee"}}><div style={{display:"flex",justifyContent:"space-between",gap:10}}><strong>{item.enabled?"● Работает":"○ На паузе"}</strong><div style={{display:"flex",gap:6}}><button disabled={!!busy} onClick={()=>void toggle(item)}>{item.enabled?"Пауза":"Включить"}</button><button disabled={!!busy} onClick={()=>void remove(item)}>Удалить</button></div></div><div style={{fontSize:12,opacity:.62,marginTop:4}}>Следующий запуск: {formatDate(item.next_run_at)}<br/>Последний запуск: {formatDate(item.last_run_at)}</div></div>)}</div>}
 </article>;
}
