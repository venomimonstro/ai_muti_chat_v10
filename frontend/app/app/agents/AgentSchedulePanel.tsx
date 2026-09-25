"use client";

import Link from "next/link";
import {useRouter} from "next/navigation";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";

type Cadence="interval"|"daily"|"weekdays"|"weekly";
type Schedule={id:string;agent:string|null;team:string|null;name:string;objective:string;enabled:boolean;cadence:Cadence;interval_minutes:number;local_time:string|null;timezone_name:string;weekdays:number[];next_run_at:string;last_run_at:string|null;last_run:string|null;skip_if_running:boolean};
type Run={id:string;state:string};
type Props={agentId:string;defaultObjective:string;active:boolean};

const presets=[[60,"Каждый час"],[360,"Каждые 6 часов"],[720,"Каждые 12 часов"],[1440,"Каждые 24 часа"]] as const;
const days=["Пн","Вт","Ср","Чт","Пт","Сб","Вс"];
const timezones=[
 ["Europe/Moscow","Москва"],
 ["Europe/Amsterdam","Амстердам / Центральная Европа"],
 ["Asia/Yekaterinburg","Екатеринбург"],
 ["Asia/Novosibirsk","Новосибирск"],
 ["Asia/Krasnoyarsk","Красноярск"],
 ["Asia/Irkutsk","Иркутск"],
 ["Asia/Vladivostok","Владивосток"],
] as const;

function cadenceText(item:Schedule){
 const value=String(item.local_time||"").slice(0,5);
 if(item.cadence==="daily")return `Каждый день в ${value}`;
 if(item.cadence==="weekdays")return `По будням в ${value}`;
 if(item.cadence==="weekly")return `${(item.weekdays||[]).map(day=>days[day]).join(", ")} в ${value}`;
 return presets.find(([minutes])=>minutes===item.interval_minutes)?.[1]||`Каждые ${item.interval_minutes} мин`;
}

export default function AgentSchedulePanel({agentId,defaultObjective,active}:Props){
 const router=useRouter();
 const[items,setItems]=useState<Schedule[]>([]);const[busy,setBusy]=useState("");const[error,setError]=useState("");const[objective,setObjective]=useState(defaultObjective);const[cadence,setCadence]=useState<Cadence>("daily");const[localTime,setLocalTime]=useState("09:00");const[interval,setIntervalValue]=useState(60);const[timezoneName,setTimezoneName]=useState("Europe/Moscow");const[weekdays,setWeekdays]=useState<number[]>([0,1,2,3,4]);
 const own=useMemo(()=>items.filter(item=>item.agent===agentId),[items,agentId]);
 const load=async()=>{try{const rows=await api<Schedule[]>("/agent-schedules/");setItems(rows);setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить расписание")}};
 useEffect(()=>{void load()},[agentId]);
 useEffect(()=>{setObjective(defaultObjective)},[defaultObjective]);
 const toggleDay=(day:number)=>setWeekdays(current=>current.includes(day)?current.filter(item=>item!==day):[...current,day].sort());
 const create=async()=>{if(!active){setError("Сначала активируйте агента");return}if(cadence==="weekly"&&!weekdays.length){setError("Выберите хотя бы один день недели");return}setBusy("create");setError("");try{const body:Record<string,unknown>={agent:agentId,team:null,name:"Автономный запуск",objective:objective.trim(),enabled:true,cadence,interval_minutes:interval,timezone_name:timezoneName,skip_if_running:true};if(cadence!=="interval")body.local_time=localTime;if(cadence==="weekly")body.weekdays=weekdays;await api("/agent-schedules/",{method:"POST",body:JSON.stringify(body)});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось создать расписание")}finally{setBusy("")}};
 const runNow=async(item:Schedule)=>{setBusy(`run:${item.id}`);setError("");try{const run=await api<Run>(`/agent-schedules/${item.id}/run-now/`,{method:"POST"});router.push(`/app/runs/${run.id}`)}catch(e){setError(e instanceof Error?e.message:"Не удалось запустить задачу");setBusy("")}};
 const toggle=async(item:Schedule)=>{setBusy(item.id);setError("");try{await api(`/agent-schedules/${item.id}/`,{method:"PATCH",body:JSON.stringify({enabled:!item.enabled})});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось изменить расписание")}finally{setBusy("")}};
 const remove=async(item:Schedule)=>{if(!window.confirm("Удалить это расписание?"))return;setBusy(item.id);setError("");try{await api(`/agent-schedules/${item.id}/`,{method:"DELETE"});await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось удалить расписание")}finally{setBusy("")}};
 const formatDate=(value:string|null)=>value?new Date(value).toLocaleString("ru-RU"):"ещё не запускался";
 return <article style={{border:"1px solid #ddd",borderRadius:18,padding:20}}>
  <div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"start"}}><div><h3 style={{marginTop:0,marginBottom:5}}>Автономный режим</h3><p style={{opacity:.65,marginTop:0}}>Сотрудник работает сам по расписанию. Параллельный дубль блокируется.</p></div><Link href="/app/schedules" style={{fontSize:12,textDecoration:"none"}}>Все расписания →</Link></div>
  {error&&<div style={{padding:9,border:"1px solid #ce747b",borderRadius:9,marginBottom:10}}>{error}</div>}
  <label style={{display:"grid",gap:5,marginBottom:9}}>Что делать при каждом запуске<textarea rows={3} value={objective} onChange={e=>setObjective(e.target.value)} placeholder="Например: подготовь один пост на актуальную тему" style={{padding:9,border:"1px solid #ccc",borderRadius:9,resize:"vertical"}}/></label>
  <label style={{display:"grid",gap:5,marginBottom:9}}>Когда<select value={cadence} onChange={e=>setCadence(e.target.value as Cadence)} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}><option value="daily">Каждый день</option><option value="weekdays">По будням</option><option value="weekly">По выбранным дням</option><option value="interval">Через интервал</option></select></label>
  {cadence==="weekly"&&<div style={{display:"flex",gap:6,flexWrap:"wrap",marginBottom:9}}>{days.map((day,index)=><button type="button" key={day} onClick={()=>toggleDay(index)} style={{padding:"7px 10px",borderRadius:999,border:"1px solid #bbb",fontWeight:weekdays.includes(index)?700:400,opacity:weekdays.includes(index)?1:.55}}>{day}</button>)}</div>}
  {cadence==="interval"?<label style={{display:"grid",gap:5}}>Интервал<select value={interval} onChange={e=>setIntervalValue(Number(e.target.value))} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}>{presets.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>:<div style={{display:"grid",gridTemplateColumns:"minmax(120px,.7fr) minmax(180px,1fr)",gap:8}}><label style={{display:"grid",gap:5}}>Время<input type="time" value={localTime} onChange={e=>setLocalTime(e.target.value)} style={{padding:8,border:"1px solid #ccc",borderRadius:9}}/></label><label style={{display:"grid",gap:5}}>Часовой пояс<select value={timezoneName} onChange={e=>setTimezoneName(e.target.value)} style={{padding:9,border:"1px solid #ccc",borderRadius:9}}>{timezones.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label></div>}
  <button onClick={()=>void create()} disabled={!!busy||!active||!objective.trim()} style={{marginTop:10,padding:"9px 12px",border:0,borderRadius:9,fontWeight:700}}>{busy==="create"?"Создаём…":"+ Добавить расписание"}</button>
  {own.length>0&&<div style={{marginTop:14,borderTop:"1px solid #eee",paddingTop:10}}>{own.map(item=><div key={item.id} style={{padding:"9px 0",borderBottom:"1px solid #eee"}}><div style={{display:"flex",justifyContent:"space-between",gap:10,alignItems:"flex-start"}}><div><strong>{item.enabled?"● Работает":"○ На паузе"}</strong><div style={{fontSize:12,opacity:.62,marginTop:4}}>{cadenceText(item)}{item.cadence!=="interval"?` · ${item.timezone_name}`:""}<br/>Следующий: {formatDate(item.next_run_at)}<br/>Последний реальный запуск: {formatDate(item.last_run_at)}</div>{item.last_run&&<Link href={`/app/runs/${item.last_run}`} style={{display:"inline-block",fontSize:12,marginTop:5,textDecoration:"none"}}>Открыть последний запуск →</Link>}</div><div style={{display:"flex",gap:6,flexWrap:"wrap",justifyContent:"flex-end"}}><button disabled={!!busy} onClick={()=>void runNow(item)}>{busy===`run:${item.id}`?"Запуск…":"Сейчас"}</button><button disabled={!!busy} onClick={()=>void toggle(item)}>{item.enabled?"Пауза":"Включить"}</button><button disabled={!!busy} onClick={()=>void remove(item)}>Удалить</button></div></div></div>)}</div>}
 </article>;
}
