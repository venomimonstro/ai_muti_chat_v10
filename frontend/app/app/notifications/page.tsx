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
 const read=async(item:Notification)=>{if(item.read_at)return;setBusy(item.id);try{await api(`/auth/notifications/${item.id}/read/`,{method:"POST"});setItems(current=>current.map(row=>row.id===item.id?{...row,read_at:new Date().toISOString()}:row));}finally{setBusy("")}};
 const readAll=async()=>{setBusy("all");try{await api("/auth/notifications/read-all/",{method:"POST"});const now=new Date().toISOString();setItems(current=>current.map(row=>({...row,read_at:row.read_at??now})));}finally{setBusy("")}};
 const unread=items.filter(item=>!item.read_at).length;
 return <main className={styles.page}><div className={styles.shell}><header className={styles.top}><div><h1>Уведомления</h1><p>{unread?`Непрочитанных: ${unread}`:"Новых уведомлений нет."}</p></div><div><button disabled={busy==="all"||unread===0} onClick={()=>void readAll()}>Прочитать все</button> <Link href="/app">← Рабочее пространство</Link></div></header>{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}<section className={styles.card}>{items.length===0?<p className={styles.muted}>Уведомлений пока нет.</p>:<div className={styles.list}>{items.map(item=><div className={styles.row} key={item.id} style={{opacity:item.read_at?.8:1}}><div style={{minWidth:0}}><b>{item.title}</b><small>{levelLabel[item.level]??"Информация"} · {new Date(item.created_at).toLocaleString("ru-RU")}</small>{item.body&&<div style={{marginTop:6,whiteSpace:"pre-wrap"}}>{item.body}</div>}{item.action_url&&<div style={{marginTop:8}}><Link href={item.action_url} onClick={()=>void read(item)}>Открыть</Link></div>}</div>{item.read_at?<span>Прочитано</span>:<button disabled={busy===item.id} onClick={()=>void read(item)}>Отметить прочитанным</button>}</div>)}</div>}</section></div></main>;
}
