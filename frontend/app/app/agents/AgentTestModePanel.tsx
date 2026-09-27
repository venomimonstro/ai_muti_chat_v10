"use client";

import {useState} from "react";
import {api} from "../../../lib/api";

type Step={sequence:number;id:string;title:string;type:string;status:string;note:string};
type TestResult={ready:boolean;paid_calls:number;external_actions:number;blockers:string[];warnings:string[];summary:{nodes:number;edges:number;simulated_steps:number;max_steps:number};steps:Step[]};
type Props={agentId:string};

const statusLabel:Record<string,string>={would_run:"будет выполнен",approval_checkpoint:"попросит подтверждение",blocked_in_test:"без внешнего действия"};

export default function AgentTestModePanel({agentId}:Props){
 const[data,setData]=useState<TestResult|null>(null);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const run=async()=>{setBusy(true);setError("");try{setData(await api<TestResult>(`/agents/${agentId}/test/`,{method:"POST",body:"{}"}))}catch(e){setError(e instanceof Error?e.message:"Не удалось выполнить тест") }finally{setBusy(false)}};
 return <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"start"}}><div><div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>Безопасная проверка</div><h3 style={{margin:"6px 0"}}>Тестовый режим</h3></div><button type="button" disabled={busy} onClick={()=>void run()} style={{padding:"9px 12px",border:"1px solid #ccc",borderRadius:9,background:"transparent",fontWeight:650}}>{busy?"Проверяем…":"Протестировать"}</button></div>
  <p style={{fontSize:13,opacity:.67}}>Показывает, как пойдёт workflow. Не вызывает модель, не списывает деньги и не выполняет публикации или уведомления.</p>
  {data&&<div style={{display:"grid",gap:7}}>{data.steps.map(step=><div key={step.id} style={{padding:"8px 10px",border:"1px solid #eee",borderRadius:9,fontSize:13}}><strong>{step.sequence}. {step.title}</strong><div style={{fontSize:12,opacity:.62,marginTop:3}}>{statusLabel[step.status]||step.status} · {step.note}</div></div>)}{data.blockers.map((item,index)=><div key={`b:${index}`} style={{fontSize:12,color:"#a24b52"}}>Блокер: {item}</div>)}{data.warnings.map((item,index)=><div key={`w:${index}`} style={{fontSize:12,opacity:.65}}>⚠ {item}</div>)}</div>}
  {error&&<div style={{fontSize:12,color:"#b33140",marginTop:8}}>{error}</div>}
 </article>;
}
