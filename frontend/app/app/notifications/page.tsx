"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../help/help.module.css";

type Notification={id:string;title:string;body:string;level:"info"|"warning"|"success";action_url:string;read_at:string|null;created_at:string};
const levelLabel:Record<string,string>={info:"Информация",warning:"Важно",success:"Готово"};

export default function NotificationsPage(){
 const[items,setItems]=useState<Notification[]>([]);const[error,setError]=useState("");const[busy,setBusy]=useState("");
 const load=async()=>{try{setItems(await api<Notification[]>("/auth/notifications/"));setError("");}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить уведомления")}};
 useEffect(()=>{void load()},[]);
 const read=async(item:Notification)=>{if(item.read_at)return;setBusy(item.id);setError("");try{await api(`/auth/notifications/${item.id}/read/`,{method:"POST"});setItems(current=>current.map(row=>row.id===item.id?{...row,read_at:new Date().toISOString()}:row));}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось отметить уведомление") }finally{setBusy("")}};
 const readAll=async()=>{setBusy("all");setError("");try{await api("/auth/notifications/read-all/",{method:"POST"});const now=new Date().toISOString();setItems(current=>current.map(row=>({...row,read_at:row.read_at??now})));}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось отметить уведомления") }finally{setBusy("")}};
 const unread=items.filter(item=>!item.read_at).length;
 return <main className={styles.page}><div className={styles.shell}><header className={styles.top}><div><span className={styles.eyebrow}>AI WORKSPACE · NOTIFICATIONS</span><h1>Уведомления</h1><p>{unread?`Непрочитанных: ${unread}`:"Новых уведомлений нет."}</p></div><div className={styles.topActions}><button disabled={busy==="all"||unread===0} onClick={()=>void readAll()}>{busy==="all"?"Сохраняем…":"Прочитать все"}</button><Link href="/app">Рабочее пространство</Link></div></header>{error&&<div className={`${styles.notice} ${styles.error}`} role="alert">{error}</div>}<section className={styles.card}>{items.length===0?<p className={styles.muted}>Уведомлений пока нет. Здесь появятся важные события продукта, платежей и автономных запусков.</p>:<div className={styles.list}>{items.map(item=><div className={`${styles.row} ${item.read_at?styles.rowRead:styles.rowUnread}`} key={item.id}><div className={styles.rowBody}><b>{item.title}</b><small>{levelLabel[item.level]??"Информация"} · {new Date(item.created_at).toLocaleString("ru-RU")}</small>{item.body&&<div className={styles.rowCopy}>{item.body}</div>}{item.action_url&&<Link className={styles.rowLink} href={item.action_url} onClick={()=>void read(item)}>Открыть →</Link>}</div>{item.read_at?<span>Прочитано</span>:<button className={styles.rowAction} disabled={busy===item.id} onClick={()=>void read(item)}>{busy===item.id?"Сохраняем…":"Прочитано"}</button>}</div>)}</div>}</section></div></main>;
}
