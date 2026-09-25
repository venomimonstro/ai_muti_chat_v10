"use client";

import Link from "next/link";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";

type Connection={id:string;kind:string;name:string;base_url:string;enabled:boolean;health_state:string;last_error?:string;last_checked_at?:string|null};
type Binding={id:string;agent:string;connection:string;connection_name:string;connection_kind:string;connection_health:string;purpose:string;enabled:boolean};
type Props={agentId:string;onChanged?:()=>void};

const healthLabel:Record<string,string>={healthy:"Работает",degraded:"Ошибка",unknown:"Не проверено",disabled:"Отключено"};

export default function AgentConnectionsPanel({agentId,onChanged}:Props){
 const[connections,setConnections]=useState<Connection[]>([]);const[bindings,setBindings]=useState<Binding[]>([]);const[selected,setSelected]=useState("");const[busy,setBusy]=useState("");const[error,setError]=useState("");const[notice,setNotice]=useState("");
 const load=async()=>{try{const[c,b]=await Promise.all([api<Connection[]>("/connections/"),api<Binding[]>(`/agent-connections/?agent=${encodeURIComponent(agentId)}`)]);setConnections(c);setBindings(b);setSelected(current=>current&&c.some(x=>x.id===current&&x.enabled&&!b.some(row=>row.connection===x.id))?current:c.find(x=>x.enabled&&x.health_state==="healthy"&&!b.some(row=>row.connection===x.id))?.id||"");setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить подключения")}};
 useEffect(()=>{void load()},[agentId]);
 const boundIds=useMemo(()=>new Set(bindings.map(item=>item.connection)),[bindings]);
 const available=connections.filter(item=>item.enabled&&!boundIds.has(item.id));
 const selectedConnection=connections.find(item=>item.id===selected)||null;
 const bind=async()=>{if(!selectedConnection)return;if(selectedConnection.health_state!=="healthy"){setError("Сначала проверьте подключение. Агенту можно выдать только рабочий внешний сервис.");return}setBusy("bind");setError("");setNotice("");try{await api("/agent-connections/",{method:"POST",body:JSON.stringify({agent:agentId,connection:selectedConnection.id,purpose:"publish",enabled:true})});setSelected("");setNotice("Подключение разрешено этому агенту.");await load();onChanged?.()}catch(e){setError(e instanceof Error?e.message:"Не удалось подключить сервис к агенту")}finally{setBusy("")}};
 const remove=async(item:Binding)=>{setBusy(item.id);setError("");setNotice("");try{await api(`/agent-connections/${item.id}/`,{method:"DELETE"});setNotice("Подключение отвязано от агента.");await load();onChanged?.()}catch(e){setError(e instanceof Error?e.message:"Не удалось отвязать сервис")}finally{setBusy("")}};
 const check=async(connectionId:string)=>{setBusy(`check:${connectionId}`);setError("");setNotice("");try{const checked=await api<Connection>(`/connections/${connectionId}/check/`,{method:"POST"});setNotice(`${checked.name}: ${checked.health_state==="healthy"?"подключение работает":"проверка завершена"}.`);await load();onChanged?.()}catch(e){setError(e instanceof Error?e.message:"Проверка подключения не прошла");await load();onChanged?.()}finally{setBusy("")}};
 const connectionFor=(binding:Binding)=>connections.find(item=>item.id===binding.connection)||null;
 return <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"start"}}><div><h3 style={{marginTop:0,marginBottom:5}}>Подключённые сервисы</h3><p style={{fontSize:13,opacity:.62,marginTop:0}}>Агент видит только явно разрешённые ему подключения. Секреты ему не передаются. Неработающее соединение автоматически блокирует внешнее действие.</p></div><Link href="/app/connections" style={{fontSize:13,textDecoration:"none"}}>Настроить →</Link></div>
  {notice&&<div style={{padding:8,border:"1px solid #7da78a",borderRadius:9,marginBottom:9}}>{notice}</div>}
  {error&&<div style={{padding:8,border:"1px solid #ce747b",borderRadius:9,marginBottom:9}}>{error}</div>}
  {bindings.map(item=>{const connection=connectionFor(item);const health=connection?.health_state||item.connection_health;return <div key={item.id} style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"center",padding:"10px 0",borderBottom:"1px solid #eee"}}><div style={{minWidth:0}}><strong>{item.connection_name}</strong><div style={{fontSize:12,opacity:health==="healthy"?.65:1,marginTop:3}}>{item.connection_kind} · {healthLabel[health]||health}</div>{connection?.last_error&&health==="degraded"&&<div style={{fontSize:12,marginTop:4,opacity:.78}}>{connection.last_error}</div>}{connection?.last_checked_at&&<div style={{fontSize:11,opacity:.5,marginTop:3}}>Проверено: {new Date(connection.last_checked_at).toLocaleString("ru-RU")}</div>}</div><div style={{display:"flex",gap:6,flexWrap:"wrap",justifyContent:"flex-end"}}>{connection&&<button disabled={!!busy} onClick={()=>void check(connection.id)}>{busy===`check:${connection.id}`?"Проверяем…":"Проверить"}</button>}<button disabled={!!busy} onClick={()=>void remove(item)}>Отвязать</button></div></div>})}
  {available.length?<div style={{marginTop:12}}><div style={{display:"flex",gap:7}}><select value={selected} onChange={e=>setSelected(e.target.value)} style={{minWidth:0,flex:1,padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="">Выберите подключение</option>{available.map(item=><option key={item.id} value={item.id}>{item.name} · {healthLabel[item.health_state]||item.health_state}</option>)}</select><button disabled={busy==="bind"||!selected||selectedConnection?.health_state!=="healthy"} onClick={()=>void bind()}>Подключить</button></div>{selectedConnection&&selectedConnection.health_state!=="healthy"&&<div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",marginTop:8,fontSize:12,opacity:.75}}><span>Перед подключением сервис должен пройти проверку.</span><button disabled={!!busy} onClick={()=>void check(selectedConnection.id)}>{busy===`check:${selectedConnection.id}`?"Проверяем…":"Проверить сейчас"}</button></div>}</div>:!bindings.length?<p style={{opacity:.6}}>Нет доступных подключений. Сначала добавьте WordPress.</p>:null}
 </article>;
}
