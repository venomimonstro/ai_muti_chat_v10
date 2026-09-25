"use client";

import Link from "next/link";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";

type Connection={id:string;kind:string;name:string;base_url:string;enabled:boolean;health_state:string};
type Binding={id:string;agent:string;connection:string;connection_name:string;connection_kind:string;connection_health:string;purpose:string;enabled:boolean};
type Props={agentId:string};

export default function AgentConnectionsPanel({agentId}:Props){
 const[connections,setConnections]=useState<Connection[]>([]);const[bindings,setBindings]=useState<Binding[]>([]);const[selected,setSelected]=useState("");const[busy,setBusy]=useState("");const[error,setError]=useState("");
 const load=async()=>{try{const[c,b]=await Promise.all([api<Connection[]>("/connections/"),api<Binding[]>(`/agent-connections/?agent=${encodeURIComponent(agentId)}`)]);setConnections(c);setBindings(b);setSelected(current=>current||c.find(x=>x.enabled&&x.health_state==="healthy")?.id||"");setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить подключения")}};
 useEffect(()=>{void load()},[agentId]);
 const boundIds=useMemo(()=>new Set(bindings.map(item=>item.connection)),[bindings]);
 const available=connections.filter(item=>item.enabled&&!boundIds.has(item.id));
 const bind=async()=>{if(!selected)return;setBusy("bind");setError("");try{await api("/agent-connections/",{method:"POST",body:JSON.stringify({agent:agentId,connection:selected,purpose:"publish",enabled:true})});setSelected("");await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось подключить сервис к агенту")}finally{setBusy("")}};
 const remove=async(item:Binding)=>{setBusy(item.id);setError("");try{await api(`/agent-connections/${item.id}/`,{method:"DELETE"});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось отвязать сервис")}finally{setBusy("")}};
 return <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"start"}}><div><h3 style={{marginTop:0,marginBottom:5}}>Подключённые сервисы</h3><p style={{fontSize:13,opacity:.62,marginTop:0}}>Агент видит только явно разрешённые ему подключения. Секреты ему не передаются.</p></div><Link href="/app/connections" style={{fontSize:13,textDecoration:"none"}}>Настроить →</Link></div>
  {error&&<div style={{padding:8,border:"1px solid #ce747b",borderRadius:9,marginBottom:9}}>{error}</div>}
  {bindings.map(item=><div key={item.id} style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",padding:"9px 0",borderBottom:"1px solid #eee"}}><div><strong>{item.connection_name}</strong><div style={{fontSize:12,opacity:.55}}>{item.connection_kind} · {item.connection_health}</div></div><button disabled={!!busy} onClick={()=>void remove(item)}>Отвязать</button></div>)}
  {available.length?<div style={{display:"flex",gap:7,marginTop:10}}><select value={selected} onChange={e=>setSelected(e.target.value)} style={{minWidth:0,flex:1,padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="">Выберите подключение</option>{available.map(item=><option key={item.id} value={item.id}>{item.name} · {item.health_state==="healthy"?"работает":"не проверено"}</option>)}</select><button disabled={busy==="bind"||!selected} onClick={()=>void bind()}>Подключить</button></div>:!bindings.length?<p style={{opacity:.6}}>Нет доступных подключений. Сначала добавьте WordPress.</p>:null}
 </article>;
}
