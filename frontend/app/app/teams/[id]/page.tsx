"use client";

import Link from "next/link";
import {useParams,useRouter} from "next/navigation";
import {useEffect,useState} from "react";
import {api} from "../../../../lib/api";
import TeamFlowMap from "../TeamFlowMap";
import styles from "./team-detail.module.css";

type Member={id:string;agent:string;agent_name:string;role:string;priority:number;can_delegate:boolean;enabled:boolean};
type Team={id:string;name:string;objective:string;kind:string;director:string;director_name:string;active:boolean;max_cost_rub_per_run:string;max_handoffs:number;members:Member[]};
type Run={id:string;state:string;objective:string;cost_actual_rub:string;step_count:number;created_at:string};

const stateLabel:Record<string,string>={queued:"В очереди",planning:"Планирование",running:"Выполняется",waiting_tool:"Ожидает инструмент",waiting_approval:"Нужно подтверждение",reviewing:"Проверка",completed:"Готово",failed:"Ошибка",canceled:"Отменён",budget_exceeded:"Лимит бюджета"};

export default function TeamDetailPage(){
 const params=useParams<{id:string}>();const router=useRouter();const id=String(params.id);
 const[team,setTeam]=useState<Team|null>(null);const[runs,setRuns]=useState<Run[]>([]);const[busy,setBusy]=useState("");const[error,setError]=useState("");const[notice,setNotice]=useState("");const[objective,setObjective]=useState("");
 const load=async()=>{try{const[t,r]=await Promise.all([api<Team>(`/agent-teams/${id}/`),api<Run[]>(`/agent-runs/?team=${encodeURIComponent(id)}`)]);setTeam(t);setRuns(r.slice(0,30));setObjective(current=>current||t.objective);setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить команду")}};
 useEffect(()=>{void load()},[id]);
 const patchTeam=async(body:Partial<Team>)=>{setBusy("team");setError("");setNotice("");try{const next=await api<Team>(`/agent-teams/${id}/`,{method:"PATCH",body:JSON.stringify(body)});setTeam(next);setNotice("Настройки команды сохранены.")}catch(e){setError(e instanceof Error?e.message:"Не удалось сохранить команду")}finally{setBusy("")}};
 const patchMember=async(member:Member,body:Partial<Member>)=>{setBusy(member.id);setError("");try{const next=await api<Team>(`/agent-teams/${id}/members/${member.id}/`,{method:"PATCH",body:JSON.stringify(body)});setTeam(next)}catch(e){setError(e instanceof Error?e.message:"Не удалось изменить участника")}finally{setBusy("")}};
 const removeMember=async(member:Member)=>{if(!window.confirm(`Удалить «${member.agent_name}» из команды?`))return;setBusy(member.id);setError("");try{const next=await api<Team>(`/agent-teams/${id}/members/${member.id}/`,{method:"DELETE"});setTeam(next)}catch(e){setError(e instanceof Error?e.message:"Не удалось удалить участника")}finally{setBusy("")}};
 const setDirector=async(member:Member)=>{setBusy(`director:${member.id}`);setError("");try{const next=await api<Team>(`/agent-teams/${id}/director/`,{method:"POST",body:JSON.stringify({agent:member.agent})});setTeam(next);setNotice(`${member.agent_name} назначен руководителем команды.`)}catch(e){setError(e instanceof Error?e.message:"Не удалось назначить руководителя")}finally{setBusy("")}};
 const run=async()=>{if(!team)return;setBusy("run");setError("");try{const started=await api<Run>(`/agent-teams/${id}/run/`,{method:"POST",body:JSON.stringify({objective:objective.trim()||team.objective})});router.push(`/app/runs/${started.id}`)}catch(e){setError(e instanceof Error?e.message:"Не удалось запустить команду")}finally{setBusy("")}};
 if(!team)return <main className={styles.page}><div className={styles.loading}>{error||"Загрузка команды…"}</div></main>;
 return <main className={styles.page}><div className={styles.shell}>
  <header className={styles.header}><div><Link href="/app/teams" className={styles.back}>← Все команды</Link><h1>{team.name}</h1><div className={styles.headerMeta}>Руководитель: {team.director_name} · {team.active?"работает":"пауза"}</div></div><button className={styles.button} onClick={()=>void patchTeam({active:!team.active})} disabled={!!busy}>{team.active?"Поставить на паузу":"Включить команду"}</button></header>
  {notice&&<div className={styles.notice} role="status">{notice}</div>}{error&&<div className={styles.error} role="alert">{error}</div>}
  <article className={`${styles.card} ${styles.flowCard}`}><span className={styles.eyebrow}>Как работает команда</span><h2>Карта передачи задачи</h2><TeamFlowMap members={team.members} director={team.director}/><div className={styles.hint}>Порядок соответствует приоритетам участников. После каждого этапа результат сохраняется как handoff и передаётся следующей роли.</div></article>
  <div className={styles.layout}>
   <section className={styles.main}>
    <article className={styles.card}><h2>Цель команды</h2><label className={styles.field}>Что команда должна получать на выходе<textarea rows={5} value={team.objective} onChange={e=>setTeam({...team,objective:e.target.value})}/></label><div className={styles.cardFooter}><button className={styles.primary} disabled={busy==="team"} onClick={()=>void patchTeam({objective:team.objective})}>{busy==="team"?"Сохраняем…":"Сохранить цель"}</button></div></article>
    <article className={styles.card}><h2>Состав и роли</h2><div className={styles.members}>{team.members.map(member=><div key={member.id} className={styles.member}><div className={styles.memberGrid}><div className={styles.memberName}><strong>{member.agent_name}</strong><span>{member.agent===team.director?"★ Руководитель":"Участник"}</span></div><input className={styles.memberRole} aria-label={`Роль ${member.agent_name}`} value={member.role} onChange={e=>setTeam({...team,members:team.members.map(x=>x.id===member.id?{...x,role:e.target.value}:x)})}/><div className={styles.memberActions}><button disabled={busy===member.id} onClick={()=>void patchMember(member,{role:member.role,enabled:member.enabled})}>{busy===member.id?"Сохраняем…":"Сохранить"}</button>{member.agent!==team.director&&<button disabled={!!busy} onClick={()=>void setDirector(member)}>{busy===`director:${member.id}`?"Назначаем…":"Руководитель"}</button>}{member.agent!==team.director&&<button disabled={!!busy} onClick={()=>void patchMember(member,{enabled:!member.enabled})}>{member.enabled?"Пауза":"Включить"}</button>}{member.agent!==team.director&&<button disabled={!!busy} onClick={()=>void removeMember(member)}>Удалить</button>}</div></div></div>)}</div></article>
    <article className={styles.card}><h2>Запустить команду</h2><label className={styles.field}>Задача этого запуска<textarea rows={4} value={objective} onChange={e=>setObjective(e.target.value)} placeholder={team.objective}/></label><div className={styles.cardFooter}><button className={styles.primary} disabled={!!busy||!team.active} onClick={()=>void run()}>{busy==="run"?"Запускаем…":"Запустить команду"}</button></div></article>
   </section>
   <aside className={styles.aside}>
    <article className={styles.card}><h3>Бюджет и координация</h3><div className={styles.budgetFields}><label className={styles.field}>Лимит на запуск, ₽<input inputMode="decimal" value={team.max_cost_rub_per_run} onChange={e=>setTeam({...team,max_cost_rub_per_run:e.target.value})}/></label><label className={styles.field}>Максимум передач между агентами<input type="number" min={1} max={500} value={team.max_handoffs} onChange={e=>setTeam({...team,max_handoffs:Number(e.target.value)})}/></label></div><div className={styles.cardFooter}><button className={styles.button} disabled={busy==="team"} onClick={()=>void patchTeam({max_cost_rub_per_run:team.max_cost_rub_per_run,max_handoffs:team.max_handoffs})}>{busy==="team"?"Сохраняем…":"Сохранить лимиты"}</button></div></article>
    <article className={styles.card}><h3>Последние запуски</h3>{runs.length?<div className={styles.runs}>{runs.slice(0,10).map(item=><Link key={item.id} href={`/app/runs/${item.id}`} className={styles.runLink}><strong>{stateLabel[item.state]||item.state}</strong><span>{item.step_count} шагов · {Number(item.cost_actual_rub||0).toFixed(2)} ₽</span></Link>)}</div>:<p className={styles.empty}>Запусков пока нет.</p>}</article>
    <Link href="/app/schedules" className={styles.scheduleLink}><strong>Автономное расписание →</strong><span>Запускайте эту команду автоматически.</span></Link>
   </aside>
  </div>
 </div></main>;
}
