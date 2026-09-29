"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type Diagnostics={
 generated_at:string;
 providers:Array<{provider:string;enabled:boolean;emergency_disabled:boolean;health:string;consecutive_failures:number;circuit_opened_until:string|null;last_checked_at:string|null;last_latency_ms:number|null;credential_source:string;keys:Record<string,number>}>;
 recent_chat_errors:Array<{generation_id:string;correlation_id:string;state:string;internal_error_code:string;provider:string;routed_model:string;actual_cost_rub:string|null;created_at:string;completed_at:string|null;attempts:Array<{sequence:number;provider__slug:string;model_slug:string;state:string;error_code:string;retryable:boolean;latency_ms:number|null}>}>;
 privacy:Record<string,boolean>;
};

const publicCodes=[
 ["AI-102","AI-канал не смог завершить запрос. Клиенту не показываются провайдер, ключ, квота или внутренний баланс."],
 ["AI-103","Сбой транспортного моста streaming/reconnect. Ответ может быть восстановлен по idempotency key."],
] as const;

export default function ChatDiagnosticsPage(){
 const[data,setData]=useState<Diagnostics|null>(null);
 const[error,setError]=useState("");
 const[copied,setCopied]=useState(false);
 const load=async()=>{try{setData(await api<Diagnostics>("/admin/chat-diagnostics/"));setError("");}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить диагностику");}};
 useEffect(()=>{void load();},[]);
 const copy=async()=>{if(!data)return;await navigator.clipboard.writeText(JSON.stringify(data,null,2));setCopied(true);window.setTimeout(()=>setCopied(false),2000);};
 const download=()=>{if(!data)return;const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=`chat-diagnostics-${new Date().toISOString().replace(/[:.]/g,"-")}.json`;a.click();URL.revokeObjectURL(url);};
 return <><header className={styles.header}><div><h1>Диагностика чата</h1><p>Быстрый снимок ошибок, AI-провайдеров и ключей без секретов и текстов переписки.</p></div><div className={styles.actions}><button className={styles.button} onClick={()=>void load()}>Обновить</button><button className={styles.button} onClick={()=>void copy()} disabled={!data}>{copied?"Скопировано":"Скопировать JSON"}</button><button className={styles.button} onClick={download} disabled={!data}>Скачать JSON</button></div></header>{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}<section className={styles.section}><h2>Коды, которые видит клиент</h2><table className={styles.table}><tbody>{publicCodes.map(([code,text])=><tr key={code}><td><b>{code}</b></td><td>{text}</td></tr>)}</tbody></table></section>{data&&<><section className={styles.section}><h2>AI-провайдеры</h2><table className={styles.table}><thead><tr><th>Провайдер</th><th>Health</th><th>Сбои подряд</th><th>Ключи</th><th>Задержка</th></tr></thead><tbody>{data.providers.map((p)=><tr key={p.provider}><td>{p.provider}</td><td>{p.health}</td><td>{p.consecutive_failures}</td><td>healthy {p.keys.healthy??0} · unknown {p.keys.unknown??0} · degraded {p.keys.degraded??0} · disabled {p.keys.disabled??0}</td><td>{p.last_latency_ms==null?"—":`${p.last_latency_ms} мс`}</td></tr>)}</tbody></table></section><section className={styles.section}><h2>Последние ошибки чата</h2><table className={styles.table}><thead><tr><th>Время</th><th>Correlation ID</th><th>Внутренний код</th><th>Провайдер</th><th>Попытки</th></tr></thead><tbody>{data.recent_chat_errors.length===0?<tr><td colSpan={5}>Ошибок нет</td></tr>:data.recent_chat_errors.map((g)=><tr key={g.generation_id}><td>{new Date(g.created_at).toLocaleString("ru-RU")}</td><td><code>{g.correlation_id}</code></td><td><code>{g.internal_error_code||"—"}</code></td><td>{g.provider||"—"}</td><td>{g.attempts.map(a=>`${a.sequence}:${a.provider__slug}:${a.error_code||a.state}`).join(" · ")||"—"}</td></tr>)}</tbody></table></section></>}</>;
}
