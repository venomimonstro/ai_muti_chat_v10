"use client";

import Link from "next/link";
import {FormEvent,useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "./help.module.css";

type SupportItem={id:string;subject:string;category:string;status:string;created_at:string};

const statusLabel:Record<string,string>={open:"Открыто",in_progress:"В работе",resolved:"Решено"};
const categoryLabel:Record<string,string>={billing:"Оплата и баланс",generation:"Ответы AI",files:"Файлы и проекты",account:"Аккаунт",other:"Другое"};
const faq=[
 ["Почему ответ остановился?","Если соединение с моделью оборвалось, уже полученный текст сохраняется. Незавершённый резерв средств освобождается автоматически."],
 ["Как выбрать нейросеть?","Оставьте AUTO · Баланс для большинства задач. Ручной выбор модели нужен только если вам принципиальна конкретная модель."],
 ["Куда пропал мой текст?","Черновик сохраняется локально и для существующего чата синхронизируется с сервером. После сбоя откройте тот же чат."],
 ["Почему файл нельзя прикрепить?","Файлы привязываются к проекту. Сначала выберите или создайте проект, затем добавьте файл."],
 ["Где посмотреть списания?","Откройте «Баланс» для истории операций или «Использование» для расходов по моделям и токенам."],
];

export default function HelpPage(){
 const[items,setItems]=useState<SupportItem[]>([]);const[notice,setNotice]=useState("");const[error,setError]=useState("");const[busy,setBusy]=useState(false);
 const load=async()=>{try{setItems(await api<SupportItem[]>("/auth/support/"));}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить обращения")}};
 useEffect(()=>{void load()},[]);
 const submit=async(e:FormEvent<HTMLFormElement>)=>{e.preventDefault();const form=e.currentTarget;const data=new FormData(form);setBusy(true);setError("");try{await api("/auth/support/",{method:"POST",body:JSON.stringify({subject:String(data.get("subject")||"").trim(),category:String(data.get("category")||"other"),message:String(data.get("message")||"").trim()})});form.reset();setNotice("Обращение отправлено. Оно появилось в истории ниже.");await load();}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось отправить обращение")}finally{setBusy(false)}};
 return <main className={styles.page}><div className={styles.shell}><header className={styles.top}><div><h1>Помощь</h1><p>Ответы на частые вопросы и связь с поддержкой.</p></div><Link href="/app">← Workspace</Link></header>{notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}<div className={styles.grid}><section className={styles.card}><h2>Частые вопросы</h2>{faq.map(([q,a])=><details key={q}><summary>{q}</summary><p>{a}</p></details>)}<div className={styles.links}><Link href="/status">Статус сервиса</Link><Link href="/faq">Все вопросы и ответы</Link></div></section><section className={styles.card}><h2>Написать в поддержку</h2><form onSubmit={submit} className={styles.form}><label>Тема<input name="subject" maxLength={160} required/></label><label>Категория<select name="category" defaultValue="other"><option value="billing">Оплата и баланс</option><option value="generation">Ответы AI</option><option value="files">Файлы и проекты</option><option value="account">Аккаунт</option><option value="other">Другое</option></select></label><label>Сообщение<textarea name="message" rows={6} maxLength={5000} required/></label><button disabled={busy}>{busy?"Отправляем…":"Отправить"}</button></form></section></div><section className={styles.card}><h2>Мои обращения</h2>{items.length===0?<p className={styles.muted}>Обращений пока нет.</p>:<div className={styles.list}>{items.map(item=><div className={styles.row} key={item.id}><div><b>{item.subject}</b><small>{categoryLabel[item.category]??"Другое"} · {new Date(item.created_at).toLocaleString("ru")}</small></div><span>{statusLabel[item.status]??item.status}</span></div>)}</div>}</section></div></main>;
}
