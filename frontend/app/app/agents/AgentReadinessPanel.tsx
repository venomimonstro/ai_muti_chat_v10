"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type Readiness={ready:boolean;checks:Record<string,boolean>;blockers:string[];warnings:string[];model:string};
type Props={agentId:string;refreshToken?:number;onChange?:(ready:boolean)=>void};

export default function AgentReadinessPanel({agentId,refreshToken=0,onChange}:Props){
 const[data,setData]=useState<Readiness|null>(null);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const load=async()=>{setBusy(true);setError("");try{const next=await api<Readiness>(`/agents/${agentId}/readiness/`);setData(next);onChange?.(next.ready)}catch(e){setError(e instanceof Error?e.message:"Не удалось проверить готовность");onChange?.(false)}finally{setBusy(false)}};
 useEffect(()=>{void load()},[agentId,refreshToken]);
 return <article style={{border:`1px solid ${data?.ready?"#8eb69a":data?"#d89a9f":"#ddd"}`,borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"start"}}><div><div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>Проверка перед запуском</div><h3 style={{margin:"6px 0"}}>{busy&&!data?"Проверяем…":data?.ready?"Готов к работе":"Нужна настройка"}</h3></div><button type="button" disabled={busy} onClick={()=>void load()} style={{padding:"8px 10px",border:"1px solid #ccc",borderRadius:9,background:"transparent"}}>{busy?"…":"Проверить"}</button></div>
  {data?.model&&<div style={{fontSize:13,opacity:.65,marginBottom:8}}>Маршрутизация модели: готова</div>}
  {data?.blockers?.length?<div style={{display:"grid",gap:6}}>{data.blockers.map((item,index)=><div key={`${index}:${item}`} style={{fontSize:13}}>• {item}</div>)}</div>:data?.ready?<p style={{fontSize:13,opacity:.68,marginBottom:0}}>Модель, карта и обязательные инструменты готовы. Платный шаг не начнётся, если эта проверка перестанет проходить.</p>:null}
  {data?.warnings?.map((item,index)=><div key={`${index}:${item}`} style={{fontSize:12,opacity:.62,marginTop:7}}>⚠ {item}</div>)}
  {error&&<div style={{fontSize:12,color:"#b33140",marginTop:8}}>{error}</div>}
 </article>;
}
