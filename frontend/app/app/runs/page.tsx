"use client";

import Link from "next/link";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";

type Approval={id:string;title:string;status:string;created_at:string;expires_at?:string};
type Run={
 id:string;agent:string|null;agent_name:string;team:string|null;team_name:string;team_kind:string;state:string;objective:string;
 cost_actual_rub:string;step_count:number;tool_call_count:number;error_code:string;error_message:string;created_at:string;updated_at:string;
 approvals:Approval[];
};

const activeStates=new Set(["queued","planning","running","waiting_tool","waiting_approval","reviewing"]);
const attentionStates=new Set(["failed","budget_exceeded"]);
const stateLabel:Record<string,string>={queued:"В очереди",planning:"Планирование",running:"Выполняется",waiting_tool:"Ожидает инструмент",waiting_approval:"Нужно подтверждение",reviewing:"Проверка",completed:"Готово",failed:"Ошибка",canceled:"Отменён",budget_exceeded:"Лимит бюджета"};
const money=(value:string)=>Number(value||0).toLocaleString("ru-RU",{minimumFractionDigits:2,maximumFractionDigits:2});
const subject=(run:Run)=>run.team_name||run.agent_name||"AI-сотрудник";

function RunRow({run}: {run:Run}){
 const pending=run.approvals?.filter(item=>item.status==="pending")||[];
 const attention=attentionStates.has(run.state)||pending.length>0;
 return <Link href={`/app/runs/${run.id}`} style={{display:"grid",gridTemplateColumns:"150px minmax(0,1fr) auto",gap:14,padding:"14px 15px",border:`1px solid ${attention?"#d7a36d":"#ddd"}`,borderRadius:13,textDecoration:"none",color:"inherit"}}>
  <div><strong>{stateLabel[run.state]||run.state}</strong><div style={{fontSize:11,opacity:.52,marginTop:4}}>{new Date(run.created_at).toLocaleString("ru-RU")}</div></div>
  <div style={{minWidth:0}}><div style={{fontSize:13,opacity:.6,marginBottom:3}}>{subject(run)}{run.team_kind==="development"?" · Dev Studio":""}</div><div style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{run.objective}</div>{pending.length>0&&<div style={{fontSize:12,marginTop:5,fontWeight:700}}>Нужно решение: {pending[0].title}</div>}{run.error_message&&<div style={{fontSize:12,color:"#b33140",marginTop:5,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{run.error_message}</div>}</div>
  <div style={{textAlign:"right",whiteSpace:"nowrap"}}><strong>{money(run.cost_actual_rub)} ₽</strong><div style={{fontSize:12,opacity:.55,marginTop:4}}>{run.step_count} шагов</div></div>
 </Link>;
}

export default function RunsInboxPage(){
 const[runs,setRuns]=useState<Run[]>([]);const[loading,setLoading]=useState(true);const[error,setError]=useState("");
 const load=async()=>{try{const data=await api<Run[]>("/agent-runs/");setRuns(data);setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить запуски")}finally{setLoading(false)}};
 useEffect(()=>{void load()},[]);
 useEffect(()=>{if(!runs.some(run=>activeStates.has(run.state)))return;const timer=window.setInterval(()=>void load(),4000);return()=>window.clearInterval(timer)},[runs]);
 const waiting=useMemo(()=>runs.filter(run=>run.state==="waiting_approval"||(run.approvals||[]).some(item=>item.status==="pending")),[runs]);
 const active=useMemo(()=>runs.filter(run=>activeStates.has(run.state)&&!waiting.some(item=>item.id===run.id)),[runs,waiting]);
 const attention=useMemo(()=>runs.filter(run=>attentionStates.has(run.state)),[runs]);
 const recent=useMemo(()=>runs.filter(run=>!activeStates.has(run.state)&&!attentionStates.has(run.state)).slice(0,30),[runs]);
 return <main style={{maxWidth:1120,margin:"0 auto",padding:"32px 20px 70px"}}>
  <header style={{display:"flex",justifyContent:"space-between",gap:18,alignItems:"flex-start",marginBottom:28}}><div><div style={{fontSize:13,opacity:.58}}>AI WORKSPACE · OPERATIONS</div><h1 style={{fontSize:38,margin:"8px 0"}}>Работа AI-сотрудников</h1><p style={{maxWidth:760,opacity:.7,fontSize:16}}>Единый журнал Agent Studio и Dev Studio. Здесь видны задачи, которые требуют решения, активная работа, ошибки и фактические расходы.</p></div><div style={{display:"flex",gap:8,flexWrap:"wrap"}}><Link href="/app/agents" style={{padding:"10px 13px",border:"1px solid #ddd",borderRadius:10,textDecoration:"none",color:"inherit"}}>Агенты</Link><Link href="/app/dev" style={{padding:"10px 13px",border:"1px solid #ddd",borderRadius:10,textDecoration:"none",color:"inherit"}}>Dev Studio</Link></div></header>
  {error&&<div style={{padding:13,border:"1px solid #cb747a",borderRadius:12,marginBottom:16}}>{error}</div>}
  {loading?<div style={{opacity:.65}}>Загрузка…</div>:<div style={{display:"grid",gap:28}}>
   <section><div style={{display:"flex",justifyContent:"space-between",alignItems:"end",marginBottom:10}}><div><h2 style={{margin:0}}>Требует решения</h2><p style={{margin:"5px 0 0",opacity:.62}}>Пока вы не подтвердите или не отклоните действие, защищённый шаг не выполняется.</p></div><strong>{waiting.length}</strong></div>{waiting.length?<div style={{display:"grid",gap:8}}>{waiting.map(run=><RunRow key={run.id} run={run}/>)}</div>:<div style={{padding:20,border:"1px dashed #bbb",borderRadius:14,opacity:.65}}>Нет задач, ожидающих вашего решения.</div>}</section>
   <section><div style={{display:"flex",justifyContent:"space-between",alignItems:"end",marginBottom:10}}><h2 style={{margin:0}}>Работает сейчас</h2><strong>{active.length}</strong></div>{active.length?<div style={{display:"grid",gap:8}}>{active.map(run=><RunRow key={run.id} run={run}/>)}</div>:<div style={{padding:20,border:"1px dashed #bbb",borderRadius:14,opacity:.65}}>Сейчас активных задач нет.</div>}</section>
   {attention.length>0&&<section><div style={{display:"flex",justifyContent:"space-between",alignItems:"end",marginBottom:10}}><div><h2 style={{margin:0}}>Требует внимания</h2><p style={{margin:"5px 0 0",opacity:.62}}>Ошибки и остановки по лимиту. Деньги сверх заданных бюджетов не должны списываться.</p></div><strong>{attention.length}</strong></div><div style={{display:"grid",gap:8}}>{attention.map(run=><RunRow key={run.id} run={run}/>)}</div></section>}
   <section><h2>Последние завершённые</h2>{recent.length?<div style={{display:"grid",gap:8}}>{recent.map(run=><RunRow key={run.id} run={run}/>)}</div>:<div style={{padding:20,border:"1px dashed #bbb",borderRadius:14,opacity:.65}}>История пока пуста.</div>}</section>
  </div>}
 </main>;
}
