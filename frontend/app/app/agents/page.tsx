"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "../../../lib/api";

type Agent={id:string;name:string;role:string;objective:string;status:string;autonomy:string;system_level:string;updated_at:string};
type Template={slug:string;name:string;role:string;objective:string;autonomy:string;system_level:string};

const statusLabel:Record<string,string>={draft:"Черновик",active:"Работает",paused:"Пауза",archived:"Архив"};
const autonomyLabel:Record<string,string>={controlled:"Контролируемый",semi_autonomous:"Полуавтономный",autonomous:"Автономный"};

export default function AgentsPage(){
 const[agents,setAgents]=useState<Agent[]>([]);const[templates,setTemplates]=useState<Template[]>([]);const[busy,setBusy]=useState("");const[error,setError]=useState("");
 const load=async()=>{try{const[a,t]=await Promise.all([api<Agent[]>("/agents/"),api<Template[]>("/agents/templates/")]);setAgents(a);setTemplates(t);setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить агентов")}};
 useEffect(()=>{void load()},[]);
 const create=async(slug:string)=>{setBusy(slug);setError("");try{await api("/agents/from-template/",{method:"POST",body:JSON.stringify({template:slug})});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось создать агента")}finally{setBusy("")}};
 return <main style={{maxWidth:1180,margin:"0 auto",padding:"32px 20px 70px"}}>
  <header style={{display:"flex",justifyContent:"space-between",gap:20,alignItems:"flex-start",marginBottom:32}}><div><div style={{fontSize:13,opacity:.6,marginBottom:8}}>AI WORKSPACE · AGENT STUDIO</div><h1 style={{fontSize:38,margin:"0 0 10px"}}>Ваши AI-сотрудники</h1><p style={{maxWidth:720,opacity:.72,fontSize:17}}>Создавайте автономных специалистов и команды обычным языком. У каждого сотрудника свои роли, инструменты, память, лимиты и карта действий.</p></div><Link href="/app" style={{textDecoration:"none",padding:"10px 14px",border:"1px solid #ddd",borderRadius:12}}>← В чат</Link></header>
  {error&&<div style={{padding:14,border:"1px solid #d66",borderRadius:12,marginBottom:20}}>{error}</div>}
  <section style={{marginBottom:34}}><div style={{display:"flex",justifyContent:"space-between",alignItems:"end",gap:12,marginBottom:14}}><div><h2 style={{margin:0}}>Нанять готового сотрудника</h2><p style={{opacity:.65,margin:"6px 0 0"}}>Выберите шаблон — затем настройте его под свой бизнес.</p></div></div><div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(230px,1fr))",gap:14}}>{templates.map(item=><article key={item.slug} style={{border:"1px solid #ddd",borderRadius:18,padding:18}}><div style={{fontSize:12,opacity:.55,textTransform:"uppercase"}}>{item.role}</div><h3 style={{fontSize:21,margin:"8px 0"}}>{item.name}</h3><p style={{opacity:.7,minHeight:88}}>{item.objective}</p><button disabled={!!busy} onClick={()=>void create(item.slug)} style={{width:"100%",padding:"11px 14px",border:0,borderRadius:11,cursor:"pointer",fontWeight:700}}>{busy===item.slug?"Создаём…":"Нанять"}</button></article>)}</div></section>
  <section><div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center",marginBottom:14}}><div><h2 style={{margin:0}}>Мои агенты</h2><p style={{opacity:.65,margin:"6px 0 0"}}>Позже здесь появятся карта действий, триггеры, память, подключения и журнал автономных запусков.</p></div><button style={{padding:"10px 14px",border:"1px solid #ddd",borderRadius:11,background:"transparent"}}>+ Создать с нуля</button></div>{!agents.length?<div style={{border:"1px dashed #bbb",borderRadius:18,padding:34,textAlign:"center",opacity:.7}}>Пока нет AI-сотрудников. Выберите готового выше.</div>:<div style={{display:"grid",gap:10}}>{agents.map(agent=><article key={agent.id} style={{display:"grid",gridTemplateColumns:"1fr auto",gap:16,border:"1px solid #ddd",borderRadius:16,padding:18}}><div><div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}><strong style={{fontSize:19}}>{agent.name}</strong><span style={{fontSize:12,padding:"4px 8px",borderRadius:999,border:"1px solid #ddd"}}>{statusLabel[agent.status]||agent.status}</span></div><div style={{opacity:.62,marginTop:5}}>{agent.role||"AI-сотрудник"} · {autonomyLabel[agent.autonomy]||agent.autonomy}</div><p style={{opacity:.72,marginBottom:0}}>{agent.objective||"Цель пока не задана"}</p></div><div style={{display:"flex",alignItems:"center"}}><Link href={`/app/agents/${agent.id}`} style={{textDecoration:"none",padding:"10px 13px",border:"1px solid #ddd",borderRadius:10}}>Открыть</Link></div></article>)}</div>}</section>
 </main>
}
