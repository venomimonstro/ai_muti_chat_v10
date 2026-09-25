"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type Version={id:string;version:number;created_at:string;created_by:string;summary:{name?:string;role?:string;autonomy?:string;system_level?:string}};

type Props={agentId:string;refreshToken?:number;onRestored:()=>Promise<void>|void};

export default function AgentVersions({agentId,refreshToken=0,onRestored}:Props){
 const[items,setItems]=useState<Version[]>([]);const[busy,setBusy]=useState("");const[error,setError]=useState("");
 const load=async()=>{try{setItems(await api<Version[]>(`/agents/${agentId}/versions/`));setError("")}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить версии")}};
 useEffect(()=>{void load()},[agentId,refreshToken]);
 const restore=async(item:Version)=>{if(!window.confirm(`Вернуть настройки агента к версии ${item.version}? Текущая конфигурация будет сохранена отдельной версией.`))return;setBusy(item.id);setError("");try{await api(`/agents/${agentId}/versions/${item.id}/restore/`,{method:"POST"});await onRestored();await load();}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось восстановить версию")}finally{setBusy("")}};
 return <div>{error&&<div style={{color:"#b33140",marginBottom:8}}>{error}</div>}{items.length===0?<p style={{opacity:.62}}>Версии появятся после первого изменения настроек.</p>:<div style={{display:"grid",gap:7}}>{items.map(item=><div key={item.id} style={{display:"grid",gridTemplateColumns:"1fr auto",gap:10,padding:"9px 0",borderBottom:"1px solid #eee"}}><div><strong>Версия {item.version}</strong><div style={{fontSize:12,opacity:.58,marginTop:3}}>{new Date(item.created_at).toLocaleString("ru-RU")} · {item.summary?.role||item.summary?.name||"Настройки агента"}</div></div><button type="button" disabled={!!busy} onClick={()=>void restore(item)} style={{padding:"7px 9px",border:"1px solid #ccc",borderRadius:8,background:"transparent"}}>{busy===item.id?"Откат…":"Восстановить"}</button></div>)}</div>}</div>;
}
