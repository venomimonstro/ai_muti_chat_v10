"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type Issue={severity:string;code:string;message:string;run_id?:string};
type Action={code:string;label:string;href:string};
type Diagnostics={ready:boolean;issues:Issue[];actions:Action[];commercial:{effective_remaining:string;active_runs:number;concurrency_limit:number}};
type Props={agentId:string;refreshToken?:number};

export default function AgentDiagnosticsPanel({agentId,refreshToken=0}:Props){
 const[data,setData]=useState<Diagnostics|null>(null);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const load=async()=>{setBusy(true);setError("");try{setData(await api<Diagnostics>(`/agents/${agentId}/diagnostics/`))}catch(e){setError(e instanceof Error?e.message:"Не удалось выполнить диагностику")}finally{setBusy(false)}};
 useEffect(()=>{void load()},[agentId,refreshToken]);
 return <article style={{border:`1px solid ${data?.ready?"#8eb69a":data?"#d8b16a":"#ddd"}`,borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"start"}}><div><div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>Самодиагностика</div><h3 style={{margin:"6px 0"}}>{data?.ready?"Проблем не найдено":"Что мешает работе"}</h3></div><button type="button" disabled={busy} onClick={()=>void load()} style={{padding:"8px 10px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>{busy?"…":"Обновить"}</button></div>
  {data?.issues.map((item,index)=><div key={`${item.code}:${index}`} style={{fontSize:13,padding:"6px 0",opacity:item.severity==="ok"?.7:1}}>{item.severity==="blocker"?"⛔ ":item.severity==="warning"?"⚠ ":item.severity==="ok"?"✓ ":"• "}{item.message}</div>)}
  {!!data?.actions.length&&<div style={{display:"grid",gap:7,marginTop:10}}>{data.actions.map(item=><Link key={item.code} href={item.href} style={{padding:"8px 10px",border:"1px solid #ddd",borderRadius:9,textDecoration:"none",color:"inherit",fontSize:13,fontWeight:650}}>{item.label} →</Link>)}</div>}
  {error&&<div style={{fontSize:12,color:"#b33140",marginTop:8}}>{error}</div>}
 </article>;
}
