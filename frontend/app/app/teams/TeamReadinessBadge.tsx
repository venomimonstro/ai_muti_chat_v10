"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type ReadinessAction={code:string;label:string;href:string};
type TeamReadiness={ready:boolean;blockers:string[];warnings:string[];actions:ReadinessAction[];models:Array<{role:string;level:string;model:string}>};
type Props={teamId:string;onChange?:(ready:boolean)=>void};

export default function TeamReadinessBadge({teamId,onChange}:Props){
 const[data,setData]=useState<TeamReadiness|null>(null);const[error,setError]=useState("");
 const load=async()=>{setError("");try{const next=await api<TeamReadiness>(`/agent-teams/${teamId}/readiness/`);setData(next);onChange?.(next.ready)}catch(e){setError(e instanceof Error?e.message:"Проверка недоступна");onChange?.(false)}};
 useEffect(()=>{void load()},[teamId]);
 if(error)return <div style={{fontSize:12,color:"#b33140",marginTop:9}}>Готовность не проверена: {error}</div>;
 if(!data)return <div style={{fontSize:12,opacity:.5,marginTop:9}}>Проверяем готовность…</div>;
 return <div style={{marginTop:9,fontSize:12}}>
  <span style={{display:"inline-block",padding:"4px 8px",borderRadius:999,border:`1px solid ${data.ready?"#8eb69a":"#d89a9f"}`,fontWeight:700}}>{data.ready?"✓ Команда готова":"Нужна настройка"}</span>
  {!data.ready&&<div style={{display:"grid",gap:6,marginTop:8}}>{data.blockers.slice(0,3).map((item,index)=><div key={`${index}:${item}`} style={{opacity:.75}}>• {item}</div>)}</div>}
  {data.ready&&data.models.length>0&&<div style={{marginTop:7,opacity:.62}}>{data.models.map(item=>`${item.role}: ${item.model}`).join(" · ")}</div>}
  {!!data.actions?.length&&<div style={{display:"flex",gap:7,flexWrap:"wrap",marginTop:9}}>{data.actions.slice(0,3).map(action=>action.href?<Link key={action.code} href={action.href} style={{padding:"6px 9px",border:"1px solid #ddd",borderRadius:9,textDecoration:"none",color:"inherit",fontWeight:650}}>{action.label} →</Link>:<span key={action.code} style={{padding:"6px 9px",border:"1px solid #e2e2e2",borderRadius:9}}>{action.label}</span>)}</div>}
  {data.warnings[0]&&<div style={{marginTop:7,opacity:.62}}>⚠ {data.warnings[0]}</div>}
 </div>;
}
