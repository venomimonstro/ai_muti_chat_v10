"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type Bucket={spent:string;limit:string;remaining:string};
type Usage={
 agent_id:string;
 currency:string;
 run:Bucket;
 day:Bucket;
 month:Bucket;
 effective_remaining:string;
 concurrency:{active:number;limit:number;available:number};
 can_start:boolean;
};
type Props={agentId:string;refreshToken?:number};

const money=(value:string)=>Number(value||0).toLocaleString("ru-RU",{minimumFractionDigits:2,maximumFractionDigits:2});

export default function AgentUsagePanel({agentId,refreshToken=0}:Props){
 const[data,setData]=useState<Usage|null>(null);const[error,setError]=useState("");
 useEffect(()=>{let active=true;const load=async()=>{try{const next=await api<Usage>(`/agents/${agentId}/usage/`);if(active){setData(next);setError("")}}catch(e){if(active)setError(e instanceof Error?e.message:"Не удалось получить расходы")}};void load();const timer=window.setInterval(()=>void load(),10000);return()=>{active=false;window.clearInterval(timer)}},[agentId,refreshToken]);
 return <article style={{border:`1px solid ${data?.can_start?"#ddd":"#d8b16a"}`,borderRadius:18,padding:20}}>
  <div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>Расходы и лимиты</div>
  <h3 style={{margin:"6px 0 12px"}}>{data?.can_start!==false?"Бюджет под контролем":"Новый запуск ограничен"}</h3>
  {data?<>
   <div style={{display:"grid",gap:8,fontSize:13}}>
    <div style={{display:"flex",justifyContent:"space-between",gap:10}}><span>За запуск</span><strong>{money(data.run.spent)} / {money(data.run.limit)} ₽</strong></div>
    <div style={{display:"flex",justifyContent:"space-between",gap:10}}><span>Сегодня</span><strong>{money(data.day.spent)} / {money(data.day.limit)} ₽</strong></div>
    <div style={{display:"flex",justifyContent:"space-between",gap:10}}><span>В этом месяце</span><strong>{money(data.month.spent)} / {money(data.month.limit)} ₽</strong></div>
    <div style={{display:"flex",justifyContent:"space-between",gap:10,paddingTop:8,borderTop:"1px solid #eee"}}><span>Доступно сейчас</span><strong>{money(data.effective_remaining)} ₽</strong></div>
    <div style={{display:"flex",justifyContent:"space-between",gap:10}}><span>Одновременные задачи</span><strong>{data.concurrency.active}/{data.concurrency.limit}</strong></div>
   </div>
   {!data.can_start&&<p style={{fontSize:12,marginBottom:0,opacity:.68}}>Новый платный запуск не начнётся, пока не освободится слот или бюджет.</p>}
  </>:<p style={{fontSize:13,opacity:.65}}>Загрузка…</p>}
  {error&&<div style={{fontSize:12,color:"#b33140",marginTop:8}}>{error}</div>}
 </article>;
}
