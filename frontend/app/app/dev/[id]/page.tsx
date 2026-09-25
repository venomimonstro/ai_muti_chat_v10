"use client";

import Link from "next/link";
import {useParams,useRouter} from "next/navigation";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../../../lib/api";

type Member={id:string;agent:string;agent_name:string;role:string;priority:number;can_delegate:boolean;enabled:boolean};
type Team={id:string;project:string|null;name:string;objective:string;kind:string;director:string;director_name:string;active:boolean;max_cost_rub_per_run:string;members:Member[]};
type Step={id:string;sequence:number;title:string;action_type:string;state:string;agent_name:string;public_log:string;cost_rub:string};
type Approval={id:string;title:string;status:string};
type Run={id:string;state:string;objective:string;cost_actual_rub:string;step_count:number;tool_call_count:number;created_at:string;steps:Step[];approvals:Approval[];output_payload?:{repository?:string;working_branch?:string;text?:string;applied_changes?:Array<{path?:string;operation?:string}>}};
type Binding={id:string;full_name:string;default_branch:string;write_enabled:boolean;private:boolean};

const stateLabel:Record<string,string>={queued:"В очереди",planning:"Планирование",running:"Выполняется",waiting_tool:"Ожидает инструмент",waiting_approval:"Нужно подтверждение",reviewing:"Проверка",completed:"Готово",failed:"Ошибка",canceled:"Отменён",budget_exceeded:"Лимит бюджета"};
const stageDefs=[
 {key:"architecture",label:"Architecture",roles:["Architecture","Engineering Director"],types:["github_read"]},
 {key:"development",label:"Development",roles:["Development"],types:["llm","code","sandbox+github_write"]},
 {key:"qa",label:"QA / Security",roles:["QA & Security"],types:["sandbox","review"]},
 {key:"review",label:"Final Review",roles:["Final Review"],types:["review"]},
] as const;

export default function DevWorkspacePage(){
 const params=useParams<{id:string}>();const router=useRouter();const id=String(params.id);
 const[team,setTeam]=useState<Team|null>(null);const[runs,setRuns]=useState<Run[]>([]);const[binding,setBinding]=useState<Binding|null>(null);const[task,setTask]=useState("");const[busy,setBusy]=useState("");const[error,setError]=useState("");
 const load=async()=>{try{const t=await api<Team>(`/agent-teams/${id}/`);setTeam(t);const r=await api<Run[]>(`/agent-runs/?team=${encodeURIComponent(id)}`);setRuns(r);if(t.project){try{setBinding(await api<Binding>(`/projects/${t.project}/github/`))}catch{setBinding(null)}}setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить Dev Project")}};
 useEffect(()=>{void load()},[id]);
 const latest=useMemo(()=>runs[0]||null,[runs]);
 useEffect(()=>{if(!latest||!["queued","planning","running","waiting_tool","reviewing"].includes(latest.state))return;const timer=window.setInterval(()=>void load(),2500);return()=>window.clearInterval(timer)},[latest?.state,id]);
 const run=async()=>{if(!team)return;setBusy("run");setError("");try{const started=await api<Run>(`/agent-teams/${id}/run/`,{method:"POST",body:JSON.stringify({objective:task.trim()||team.objective})});setTask("");router.push(`/app/runs/${started.id}`)}catch(e){setError(e instanceof Error?e.message:"Не удалось запустить задачу")}finally{setBusy("")}};
 const stageState=(stage:(typeof stageDefs)[number])=>{if(!latest)return "not_started";const related=latest.steps.filter(step=>stage.roles.includes(step.title as never)||stage.types.includes(step.action_type as never));if(!related.length)return latest.state==="completed"?"completed":"not_started";if(related.some(step=>step.state==="failed"))return "failed";if(related.some(step=>["running","waiting_approval"].includes(step.state)))return "running";if(related.every(step=>step.state==="completed"))return "completed";return "pending"};
 if(!team)return <main style={{padding:32}}>{error||"Загрузка…"}</main>;
 return <main style={{maxWidth:1220,margin:"0 auto",padding:"30px 20px 70px"}}>
  <header style={{display:"flex",justifyContent:"space-between",gap:18,alignItems:"flex-start",marginBottom:26}}><div><Link href="/app/dev" style={{textDecoration:"none",opacity:.65}}>← Dev Studio</Link><h1 style={{fontSize:36,margin:"10px 0 6px"}}>{team.name}</h1><div style={{opacity:.62}}>{binding?.full_name||"GitHub repository"} · руководитель {team.director_name}</div></div><div style={{display:"flex",gap:8}}>{latest&&<Link href={`/app/runs/${latest.id}`} style={{padding:"10px 13px",border:"1px solid #ddd",borderRadius:10,textDecoration:"none",color:"inherit"}}>Открыть последний run</Link>}</div></header>
  {error&&<div style={{padding:12,border:"1px solid #cb747a",borderRadius:11,marginBottom:14}}>{error}</div>}
  <section style={{display:"grid",gridTemplateColumns:"repeat(4,minmax(0,1fr))",gap:12,marginBottom:22}}>{stageDefs.map((stage,index)=>{const state=stageState(stage);return <article key={stage.key} style={{border:"1px solid #ddd",borderRadius:16,padding:16}}><div style={{fontSize:12,opacity:.48}}>ЭТАП {index+1}</div><strong style={{display:"block",fontSize:18,margin:"6px 0"}}>{stage.label}</strong><span style={{fontSize:13,opacity:.65}}>{state==="completed"?"✓ Готово":state==="running"?"● В работе":state==="failed"?"Ошибка":state==="pending"?"Ожидает":"Не начат"}</span></article>})}</section>
  <div style={{display:"grid",gridTemplateColumns:"minmax(0,1.25fr) minmax(300px,.75fr)",gap:18}}>
   <section style={{display:"grid",gap:16}}>
    <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}><h2 style={{marginTop:0}}>Текущая задача</h2><p style={{whiteSpace:"pre-wrap",opacity:.76}}>{latest?.objective||team.objective}</p>{latest&&<div style={{display:"flex",gap:12,flexWrap:"wrap",fontSize:13,opacity:.62}}><span>Статус: <strong>{stateLabel[latest.state]||latest.state}</strong></span><span>Шагов: {latest.step_count}</span><span>Инструментов: {latest.tool_call_count}</span><span>Расход: {Number(latest.cost_actual_rub||0).toFixed(2)} ₽</span></div>}{latest?.approvals?.some(item=>item.status==="pending")&&<div style={{marginTop:12,padding:12,border:"1px solid #d8b16a",borderRadius:11}}>Нужно подтверждение пользователя. <Link href={`/app/runs/${latest.id}`}>Открыть запрос →</Link></div>}</article>
    {latest?.steps?.length?<article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}><h2 style={{marginTop:0}}>Ход разработки</h2><div style={{display:"grid",gap:9}}>{latest.steps.map(step=><div key={step.id} style={{display:"grid",gridTemplateColumns:"34px minmax(0,1fr) auto",gap:10,alignItems:"start",padding:"10px 0",borderBottom:"1px solid #eee"}}><div style={{width:30,height:30,borderRadius:999,border:"1px solid #bbb",display:"grid",placeItems:"center"}}>{step.sequence}</div><div><strong>{step.title}</strong><div style={{fontSize:12,opacity:.58,marginTop:3}}>{step.agent_name} · {stateLabel[step.state]||step.state}</div>{step.public_log&&<div style={{fontSize:13,opacity:.72,marginTop:5,maxHeight:74,overflow:"hidden"}}>{step.public_log}</div>}</div><span style={{fontSize:12,opacity:.55}}>{Number(step.cost_rub||0).toFixed(2)} ₽</span></div>)}</div></article>:null}
    <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}><h2 style={{marginTop:0}}>Новая задача проекту</h2><textarea rows={5} value={task} onChange={e=>setTask(e.target.value)} placeholder="Опишите следующий результат: исправить баг, добавить функцию, провести аудит..." style={{width:"100%",boxSizing:"border-box",padding:12,border:"1px solid #ccc",borderRadius:10,resize:"vertical"}}/><button disabled={!!busy||!team.active} onClick={()=>void run()} style={{marginTop:10,padding:"11px 15px",border:0,borderRadius:10,fontWeight:700}}>{busy==="run"?"Запускаем…":"Запустить команду"}</button></article>
   </section>
   <aside style={{display:"grid",gap:16,alignContent:"start"}}>
    <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}><h3 style={{marginTop:0}}>GitHub</h3>{binding?<><strong>{binding.full_name}</strong><div style={{fontSize:13,opacity:.62,marginTop:5}}>Default: {binding.default_branch}<br/>{binding.private?"Private repository":"Public repository"}<br/>{binding.write_enabled?"Запись разрешена":"Только чтение"}</div>{latest?.output_payload?.working_branch&&<div style={{marginTop:10,padding:9,border:"1px solid #ddd",borderRadius:9,fontSize:13}}>Рабочая ветка<br/><strong>{latest.output_payload.working_branch}</strong></div>}</>:<p style={{opacity:.65}}>Repository binding не найден.</p>}</article>
    <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}><h3 style={{marginTop:0}}>Команда</h3>{team.members.map(member=><div key={member.id} style={{padding:"8px 0",borderBottom:"1px solid #eee"}}><strong>{member.role}</strong><div style={{fontSize:12,opacity:.58}}>{member.agent_name}{member.can_delegate?" · может делегировать":""}</div></div>)}</article>
    <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}><h3 style={{marginTop:0}}>История</h3>{runs.length?runs.slice(0,8).map(run=><Link key={run.id} href={`/app/runs/${run.id}`} style={{display:"block",padding:"8px 0",borderBottom:"1px solid #eee",textDecoration:"none",color:"inherit"}}><strong>{stateLabel[run.state]||run.state}</strong><div style={{fontSize:12,opacity:.55}}>{new Date(run.created_at).toLocaleString("ru-RU")} · {Number(run.cost_actual_rub||0).toFixed(2)} ₽</div></Link>):<p style={{opacity:.65}}>Запусков пока нет.</p>}</article>
   </aside>
  </div>
 </main>;
}
