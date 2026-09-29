"use client";

import Link from "next/link";
import {useRouter} from "next/navigation";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";
import type {Project} from "../../../lib/types";

type Binding={id:string;repository_id:number;full_name:string;default_branch:string;private:boolean;write_enabled:boolean;last_synced_at:string|null};
type ProjectSetup={project:Project;binding:Binding|null};
type GitHubHealth={healthy:boolean;read_ok:boolean;write_ready:boolean;write_enabled:boolean;contents_permission:string;repository:string;default_branch:string;root_items:number;checked_at:string;error:string};
type TeamMember={id:string;role:string;enabled:boolean};
type Team={id:string;project:string|null;name:string;objective:string;kind:string;active:boolean;max_cost_rub_per_run:string;members:TeamMember[]};
type Run={id:string;team:string|null;state:string;objective:string;created_at:string};

const stateLabel:Record<string,string>={queued:"В очереди",planning:"Планирует",running:"Разрабатывает",waiting_tool:"Ждёт инструмент",waiting_approval:"Ждёт решения",reviewing:"Проверяет",completed:"Готово",failed:"Ошибка",canceled:"Остановлен",budget_exceeded:"Лимит бюджета"};

export default function DevStudioV2(){
 const router=useRouter();
 const[projects,setProjects]=useState<ProjectSetup[]>([]);const[selectedProject,setSelectedProject]=useState("");const[health,setHealth]=useState<GitHubHealth|null>(null);const[healthLoading,setHealthLoading]=useState(false);const[teams,setTeams]=useState<Team[]>([]);const[runs,setRuns]=useState<Run[]>([]);const[objective,setObjective]=useState("");const[loading,setLoading]=useState(true);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const devTeams=useMemo(()=>teams.filter(team=>team.kind==="development"),[teams]);
 const runByTeam=useMemo(()=>{const map=new Map<string,Run>();for(const run of runs){if(run.team&&!map.has(run.team))map.set(run.team,run)}return map},[runs]);
 const projectName=useMemo(()=>Object.fromEntries(projects.map(item=>[item.project.id,item.project.name])),[projects]);
 const selected=projects.find(item=>item.project.id===selectedProject)??null;
 const selectedBindingId=selected?.binding?.id??"";
 const taskReady=objective.trim().length>=12;
 const canCreate=Boolean(selected?.binding&&health?.write_ready&&taskReady&&!busy&&!healthLoading);

 const load=async()=>{setLoading(true);try{const[projectRows,teamRows,runRows]=await Promise.all([api<Project[]>("/projects/"),api<Team[]>("/agent-teams/"),api<Run[]>("/agent-runs/")]);setTeams(teamRows);setRuns(runRows);const owned=projectRows.filter(item=>item.role==="owner"&&!item.archived_at);const setups=await Promise.all(owned.map(async project=>{try{return {project,binding:await api<Binding>(`/projects/${project.id}/github/`)};}catch{return {project,binding:null}}}));setProjects(setups);setSelectedProject(current=>current&&setups.some(item=>item.project.id===current)?current:setups[0]?.project.id||"");setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить Dev Studio")}finally{setLoading(false)}};
 useEffect(()=>{void load()},[]);
 useEffect(()=>{let active=true;if(!selectedProject||!selected?.binding){setHealth(null);setHealthLoading(false);return()=>{active=false}}setHealthLoading(true);setHealth(null);void api<GitHubHealth>(`/projects/${selectedProject}/github/health/`).then(value=>{if(active)setHealth(value)}).catch(e=>{if(active)setHealth({healthy:false,read_ok:false,write_ready:false,write_enabled:false,contents_permission:"unknown",repository:"",default_branch:"",root_items:0,checked_at:new Date().toISOString(),error:e instanceof Error?e.message:"Не удалось проверить подключение"})}).finally(()=>{if(active)setHealthLoading(false)});return()=>{active=false}},[selectedProject,selectedBindingId]);
 useEffect(()=>{if(!runs.some(run=>["queued","planning","running","reviewing","waiting_tool"].includes(run.state)))return;const timer=window.setInterval(()=>void api<Run[]>("/agent-runs/").then(setRuns).catch(()=>undefined),5000);return()=>window.clearInterval(timer)},[runs]);

 const create=async()=>{if(!selectedProject){setError("Сначала создайте или выберите проект");return}if(!selected?.binding){setError("Сначала подключите GitHub к выбранному проекту");return}if(!health?.write_ready){setError("GitHub выбранного проекта пока не готов к безопасной работе Dev Studio");return}if(!taskReady){setError("Опишите результат задачи чуть подробнее — минимум одним понятным предложением");return}setBusy(true);setError("");try{const team=await api<Team>("/agent-teams/bootstrap-dev/",{method:"POST",body:JSON.stringify({objective:objective.trim(),project:selectedProject})});router.push(`/app/dev/${team.id}`)}catch(e){setError(e instanceof Error?e.message:"Не удалось создать задачу разработки")}finally{setBusy(false)}};
 const connectionText=!selected?"Сначала создайте проект":!selected.binding?"Подключите код проекта":healthLoading?"Проверяем GitHub…":health?.write_ready?"Всё готово":health?.read_ok?"Нужно разрешить безопасные изменения":"Нужно восстановить подключение";
 const actionLabel=busy?"Создаём…":!selected?"Сначала создайте проект":!selected.binding?"Сначала подключите GitHub":healthLoading?"Проверяем подключение…":!health?.write_ready?"Исправьте подключение":!taskReady?"Опишите задачу":"Создать задачу разработки";

 return <main className="studioPage devStudioSimple">
  <header className="studioHero"><div><span className="studioEyebrow">DEV STUDIO</span><h1>Разработка без сложной панели управления</h1><p>Выберите проект, опишите результат обычными словами — система сама соберёт команду, проверит доступ к коду и проведёт задачу через разработку и проверку.</p></div><div className="studioHeroActions"><Link href="/app/runs">История задач</Link><Link href="/app/projects">Проекты</Link></div></header>
  {error&&<div className="studioAlert error" role="alert">{error}</div>}
  <section className="devStartGrid"><div className="studioCreateCard"><div className="studioCreateIntro"><div><span>Новая задача</span><h2>Что нужно сделать?</h2></div></div>
   {projects.length?<label className="studioField"><span>Проект</span><select value={selectedProject} onChange={e=>setSelectedProject(e.target.value)}>{projects.map(item=><option key={item.project.id} value={item.project.id}>{item.project.name}{item.binding?"":" · нужен GitHub"}</option>)}</select></label>:<div className="studioSetup"><b>У вас пока нет проекта</b><span>Создайте проект — он объединит чаты, файлы, инструкции и код в один рабочий контекст.</span><Link href="/app/projects">Создать проект →</Link></div>}
   <textarea className="studioPrompt" rows={6} value={objective} onChange={e=>setObjective(e.target.value)} placeholder="Например: исправь ошибку авторизации в личном кабинете, проверь связанные сценарии и подготовь безопасное обновление." aria-label="Задача для команды разработки"/>
   <div className="studioCreateFooter"><span>{selected?.binding&&health?.write_ready?"Доступ к коду проверен. После создания задачи вы увидите ход работы, запросы подтверждения и итоговый результат.":"Dev Studio ничего не меняет в коде, пока подключение проекта не прошло проверку."}</span><button className="studioPrimary" onClick={()=>void create()} disabled={!canCreate}>{actionLabel}</button></div>
  </div>
  <aside className={`studioConnection ${selected&&(!selected.binding||health&&!health.write_ready)?"warning":""}`}><span className="studioEyebrow">ПОДГОТОВКА</span><h3>{connectionText}</h3><div className="studioFlow">
   <div className={`studioFlowStep ${selected?"done":"current"}`}><span>1</span><div><b>Проект</b><small>{selected?selected.project.name:"Создайте первый проект"}</small></div></div>
   <div className={`studioFlowStep ${selected?.binding&&(health?.write_ready||healthLoading)?"done":selected?"current":""}`}><span>2</span><div><b>GitHub</b><small>{!selected?"После выбора проекта":!selected.binding?"Подключите репозиторий":healthLoading?"Проверяем доступ":health?.write_ready?selected.binding.full_name:"Требуется настройка"}</small></div></div>
   <div className={`studioFlowStep ${selected?.binding&&health?.write_ready?(taskReady?"done":"current"):""}`}><span>3</span><div><b>Задача</b><small>{taskReady?"Описание готово":"Опишите нужный результат"}</small></div></div>
  </div>
  {selected&&!selected.binding&&<Link className="studioNextAction" href="/app/projects">Подключить GitHub к проекту →</Link>}{selected?.binding&&!healthLoading&&!health?.write_ready&&<Link className="studioNextAction" href="/app/projects">Исправить подключение →</Link>}{selected?.binding&&health?.write_ready&&<p className="studioReadyText">Можно начинать. Технические параметры скрыты — система проверяет их автоматически.</p>}
  {selected?.binding&&<details><summary>Технические детали</summary><dl><div><dt>Чтение кода</dt><dd>{health?.read_ok?"Готово":healthLoading?"Проверяем":"Нет"}</dd></div><div><dt>Изменение кода</dt><dd>{health?.write_ready?"Готово":healthLoading?"Проверяем":"Не готово"}</dd></div><div><dt>Ветка</dt><dd>{selected.binding.default_branch}</dd></div>{health?.error&&<div><dt>Ошибка</dt><dd>{health.error}</dd></div>}</dl></details>}
  </aside></section>
  <section className="studioSection"><div className="studioSectionHead"><div><h2>Текущая разработка</h2><p>Возвращайтесь к задаче — настройки команды и технический контекст уже сохранены.</p></div></div>{loading?<div className="studioEmpty">Загружаем задачи…</div>:devTeams.length?<div className="studioCards">{devTeams.map(team=>{const last=runByTeam.get(team.id);return <Link href={`/app/dev/${team.id}`} className="studioCard" key={team.id}><div className="studioCardTop"><span className="studioStatus active">{last?stateLabel[last.state]||last.state:"Готов"}</span><small>{projectName[team.project||""]||"Проект"}</small></div><h3>{team.name}</h3><p>{team.objective}</p><span className="studioOpen">Открыть задачу →</span></Link>})}</div>:<div className="studioEmpty">Пока нет задач разработки. Когда GitHub проекта будет готов, опишите первую задачу выше.</div>}</section>
 </main>;
}
