"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type ReadinessAction={code:string;label:string;href:string};
type Readiness={ready:boolean;checks:Record<string,boolean>;blockers:string[];warnings:string[];actions:ReadinessAction[];model:string};
type Props={agentId:string;refreshToken?:number;onChange?:(ready:boolean)=>void};

export default function AgentReadinessPanel({agentId,refreshToken=0,onChange}:Props){
 const[data,setData]=useState<Readiness|null>(null);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const load=async()=>{setBusy(true);setError("");try{const next=await api<Readiness>(`/agents/${agentId}/readiness/`);setData(next);onChange?.(next.ready)}catch(e){setError(e instanceof Error?e.message:"Не удалось проверить готовность");onChange?.(false)}finally{setBusy(false)}};
 useEffect(()=>{void load()},[agentId,refreshToken]);
 return <article style={{border:`1px solid ${data?.ready?"#8eb69a":data?"#d89a9f":"#ddd"}`,borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"start"}}><div><div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>Проверка перед запуском</div><h3 style={{margin:"6px 0"}}>{busy&&!data?"Проверяем…":data?.ready?"Готов к работе":"Нужна настройка"}</h3></div><button type="button" disabled={busy} onClick={()=>void load()} style={{padding:"8px 10px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>{busy?"…":"Проверить"}</button></div>
  {data?.model&&<div style={{fontSize:13,opacity:.65,marginBottom:8}}>Уровень: <strong>{data.model}</strong> · маршрутизация готова</div>}
  {data?.blockers?.length?<div style={{display:"grid",gap:6}}>{data.blockers.map((item,index)=><div key={`${index}:${item}`} style={{fontSize:13}}>• {item}</div>)}</div>:data?.ready?<p style={{fontSize:13,opacity:.68,marginBottom:0}}>Карта и обязательные инструменты готовы. Платный шаг не начнётся, если эта проверка перестанет проходить.</p>:null}
  {!!data?.actions?.length&&<div style={{display:"grid",gap:7,marginTop:12}}>{data.actions.map(action=>action.href?<Link key={action.code} href={action.href} style={{display:"block",padding:"9px 11px",border:"1px solid #ddd",borderRadius:10,textDecoration:"none",color:"inherit",fontSize:13,fontWeight:650}}>{action.label} →</Link>:<div key={action.code} style={{padding:"9px 11px",border:"1px solid #e2e2e2",borderRadius:10,fontSize:13}}>{action.label}</div>)}</div>}
  {data?.warnings?.map((item,index)=><div key={`${index}:${item}`} style={{fontSize:12,opacity:.62,marginTop:7}}>⚠ {item}</div>)}
  {error&&<div style={{fontSize:12,color:"#b33140",marginTop:8}}>{error}</div>}
 </article>;
}
