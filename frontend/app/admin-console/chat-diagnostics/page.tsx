"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type Provider={provider:string;enabled:boolean;emergency_disabled:boolean;health:string;consecutive_failures:number;circuit_opened_until:string|null;last_checked_at:string|null;last_latency_ms:number|null;credential_source:string;keys:Record<string,number>;key_error_codes:Record<string,number>};
type ChatError={generation_id:string;correlation_id:string;state:string;internal_error_code:string;provider:string;routed_model:string;actual_cost_rub:string|null;created_at:string;completed_at:string|null;attempts:Array<{sequence:number;provider__slug:string;model_slug:string;state:string;error_code:string;retryable:boolean;latency_ms:number|null}>};
type SystemIssue={fingerprint:string;status:string;severity:string;exception_type:string;summary:string;source:string;method:string;task_id:string;correlation_id:string;first_seen_at:string;last_seen_at:string;occurrences:number;resolution_note:string};
type Diagnostics={schema_version:number;generated_at:string;summary:{open_system_issues:number;recent_failed_generations:number;unhealthy_providers:number;providers_total:number};providers:Provider[];recent_chat_errors:ChatError[];system_issues:SystemIssue[];privacy:Record<string,boolean>};

export default function ChatDiagnosticsPage(){
 const[data,setData]=useState<Diagnostics|null>(null);
 const[error,setError]=useState("");
 const[copied,setCopied]=useState(false);
 const[shareUrl,setShareUrl]=useState("");
 const[sharing,setSharing]=useState(false);
 const load=async()=>{try{setData(await api<Diagnostics>("/admin/chat-diagnostics/"));setError("");}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить диагностику");}};
 useEffect(()=>{void load();},[]);
 const copy=async()=>{if(!data)return;await navigator.clipboard.writeText(JSON.stringify(data,null,2));setCopied(true);window.setTimeout(()=>setCopied(false),2000);};
 const share=async()=>{setSharing(true);setError("");try{const result=await api<{url:string;expires_in_seconds:number}>("/admin/chat-diagnostics/share/",{method:"POST",body:"{}"});setShareUrl(result.url);await navigator.clipboard.writeText(result.url);}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось создать диагностическую ссылку");}finally{setSharing(false);}};
 const download=()=>{if(!data)return;const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=`system-diagnostics-${new Date().toISOString().replace(/[:.]/g,"-")}.json`;a.click();URL.revokeObjectURL(url);};
 return <>
  <header className={styles.header}><div><h1>Диагностический центр</h1><p>Единый безопасный снимок ошибок системы, клиентского интерфейса, AI-провайдеров и неудачных генераций.</p></div><div className={styles.actions}><button className={styles.button} onClick={()=>void load()}>Обновить</button><button className={styles.button} onClick={()=>void share()} disabled={sharing}>{sharing?"Создаём…":"Сгенерировать ссылку"}</button><button className={styles.button} onClick={()=>void copy()} disabled={!data}>{copied?"Скопировано":"Скопировать JSON"}</button><button className={styles.button} onClick={download} disabled={!data}>Скачать JSON</button></div></header>
  {error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  {shareUrl&&<section className={styles.section}><h2>Ссылка для диагностики</h2><p>Действует 6 часов. В отчёте нет API-ключей, паролей, текстов переписки и персональных данных.</p><code style={{wordBreak:"break-all"}}>{shareUrl}</code><div className={styles.actions}><button className={styles.button} onClick={()=>void navigator.clipboard.writeText(shareUrl)}>Скопировать ссылку</button></div></section>}
  {data&&<>
   <section className={styles.section}><h2>Сводка</h2><table className={styles.table}><tbody><tr><td>Открытые системные ошибки</td><td><b>{data.summary.open_system_issues}</b></td></tr><tr><td>Последние неудачные генерации</td><td><b>{data.summary.recent_failed_generations}</b></td></tr><tr><td>Проблемные провайдеры</td><td><b>{data.summary.unhealthy_providers} / {data.summary.providers_total}</b></td></tr><tr><td>Снимок</td><td>{new Date(data.generated_at).toLocaleString("ru-RU")}</td></tr></tbody></table></section>
   <section className={styles.section}><h2>AI-провайдеры</h2><table className={styles.table}><thead><tr><th>Провайдер</th><th>Health</th><th>Сбои</th><th>Ключи</th><th>Ошибки ключей</th><th>Задержка</th></tr></thead><tbody>{data.providers.map((p)=><tr key={p.provider}><td>{p.provider}</td><td>{p.health}{p.emergency_disabled?" · emergency off":""}</td><td>{p.consecutive_failures}</td><td>healthy {p.keys.healthy??0} · unknown {p.keys.unknown??0} · degraded {p.keys.degraded??0}</td><td>{Object.entries(p.key_error_codes).map(([code,count])=>`${code} ×${count}`).join(" · ")||"—"}</td><td>{p.last_latency_ms==null?"—":`${p.last_latency_ms} мс`}</td></tr>)}</tbody></table></section>
   <section className={styles.section}><h2>Системные ошибки</h2><table className={styles.table}><thead><tr><th>Последняя</th><th>Статус</th><th>Ошибка</th><th>Источник</th><th>Повторов</th><th>Correlation ID</th></tr></thead><tbody>{data.system_issues.length===0?<tr><td colSpan={6}>Ошибок нет</td></tr>:data.system_issues.map((issue)=><tr key={issue.fingerprint}><td>{new Date(issue.last_seen_at).toLocaleString("ru-RU")}</td><td>{issue.severity} · {issue.status}</td><td><b>{issue.exception_type}</b><br/><small>{issue.summary}</small></td><td><code>{issue.source}</code></td><td>{issue.occurrences}</td><td><code>{issue.correlation_id||"—"}</code></td></tr>)}</tbody></table></section>
   <section className={styles.section}><h2>Неудачные генерации</h2><table className={styles.table}><thead><tr><th>Время</th><th>Correlation ID</th><th>Код</th><th>Провайдер / модель</th><th>Попытки</th></tr></thead><tbody>{data.recent_chat_errors.length===0?<tr><td colSpan={5}>Ошибок нет</td></tr>:data.recent_chat_errors.map((g)=><tr key={g.generation_id}><td>{new Date(g.created_at).toLocaleString("ru-RU")}</td><td><code>{g.correlation_id}</code></td><td><code>{g.internal_error_code||"—"}</code></td><td>{g.provider||"—"} / {g.routed_model||"—"}</td><td>{g.attempts.map(a=>`${a.sequence}:${a.provider__slug}:${a.model_slug}:${a.error_code||a.state}`).join(" · ")||"—"}</td></tr>)}</tbody></table></section>
  </>}
 </>;
}
