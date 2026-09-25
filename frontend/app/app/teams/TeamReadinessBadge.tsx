"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type TeamReadiness={ready:boolean;blockers:string[];warnings:string[];models:Array<{role:string;level:string;model:string}>};
type Props={teamId:string;onChange?:(ready:boolean)=>void};

export default function TeamReadinessBadge({teamId,onChange}:Props){
 const[data,setData]=useState<TeamReadiness|null>(null);const[error,setError]=useState("");
 const load=async()=>{setError("");try{const next=await api<TeamReadiness>(`/agent-teams/${teamId}/readiness/`);setData(next);onChange?.(next.ready)}catch(e){setError(e instanceof Error?e.message:"Проверка недоступна");onChange?.(false)}};
 useEffect(()=>{void load()},[teamId]);
 if(error)return <div style={{fontSize:12,color:"#b33140",marginTop:9}}>Готовность не проверена: {error}</div>;
 if(!data)return <div style={{fontSize:12,opacity:.5,marginTop:9}}>Проверяем готовность…</div>;
 return <div style={{marginTop:9,fontSize:12}}><span style={{display:"inline-block",padding:"4px 8px",borderRadius:999,border:`1px solid ${data.ready?"#8eb69a":"#d89a9f"}`,fontWeight:700}}>{data.ready?"✓ Команда готова":"Нужна настройка"}</span>{!data.ready&&data.blockers[0]&&<span style={{marginLeft:8,opacity:.72}}>{data.blockers[0]}</span>}{data.ready&&data.warnings[0]&&<span style={{marginLeft:8,opacity:.62}}>⚠ {data.warnings[0]}</span>}</div>;
}
