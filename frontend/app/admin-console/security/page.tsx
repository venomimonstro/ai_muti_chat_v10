"use client";

import {FormEvent,useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type Event={id:string;category:string;severity:string;status:string;user_id:string|null;ip_address:string|null;summary:string;details:Record<string,unknown>;created_at:string;resolved_at:string|null};
type ChatHit={message_id:string;conversation_id:string;conversation_title:string;user_id:string;username:string;email:string;user_status:string;role:string;matched_terms:string[];content:string;created_at:string};
type ConversationData={conversation:{id:string;title:string;user_id:string;username:string;email:string;user_status:string;created_at:string};messages:{id:string;role:string;status:string;content:string;created_at:string}[];truncated:boolean};
const statusLabel:Record<string,string>={open:"Открыто",investigating:"Расследуется",resolved:"Закрыто"};
const severityLabel:Record<string,string>={info:"Информация",warning:"Предупреждение",critical:"Критическое"};

export default function SecurityAdmin(){
  const[items,setItems]=useState<Event[]>([]);
  const[status,setStatus]=useState("");
  const[error,setError]=useState("");
  const[busy,setBusy]=useState(false);
  const[terms,setTerms]=useState("");
  const[hits,setHits]=useState<ChatHit[]>([]);
  const[chat,setChat]=useState<ConversationData|null>(null);

  const load=async()=>{try{setItems(await api<Event[]>(`/admin/security/?limit=200${status?`&status=${status}`:""}`));setError("");}catch(r){setError(r instanceof Error?r.message:"Не удалось загрузить события безопасности");}};
  useEffect(()=>{void load();},[status]);

  const act=async(id:string,action:"investigate"|"resolve"|"contain")=>{if(action==="contain"&&!window.confirm("Изоляция заблокирует связанного пользователя и отзовёт его API-ключи. Продолжить?"))return;setBusy(true);try{await api(`/admin/security/${id}/action/`,{method:"POST",body:JSON.stringify({action})});await load();}catch(r){setError(r instanceof Error?r.message:"Действие не выполнено");}finally{setBusy(false);}};

  const create=async(e:FormEvent<HTMLFormElement>)=>{e.preventDefault();const f=new FormData(e.currentTarget);setBusy(true);try{await api("/admin/security/",{method:"POST",body:JSON.stringify({category:f.get("category"),summary:f.get("summary"),severity:f.get("severity"),user_id:f.get("user_id")||null,ip_address:f.get("ip_address")||null})});e.currentTarget.reset();await load();}catch(r){setError(r instanceof Error?r.message:"Событие не создано");}finally{setBusy(false);}};

  const searchChats=async()=>{const cleaned=terms.trim();if(!cleaned){setHits([]);return;}setBusy(true);try{const result=await api<{results:ChatHit[]}>(`/admin/safety/chats/search/?terms=${encodeURIComponent(cleaned)}`);setHits(result.results);setChat(null);setError("");}catch(r){setError(r instanceof Error?r.message:"Поиск по чатам не выполнен");}finally{setBusy(false);}};
  const openChat=async(id:string)=>{setBusy(true);try{setChat(await api<ConversationData>(`/admin/safety/conversations/${id}/`));setError("");}catch(r){setError(r instanceof Error?r.message:"Не удалось открыть переписку");}finally{setBusy(false);}};
  const blockUser=async(userId:string)=>{if(!window.confirm("Заблокировать пользователя, отозвать API-ключи и активные сессии?"))return;setBusy(true);try{await api(`/admin/users/${userId}/action/`,{method:"POST",body:JSON.stringify({action:"block",reason:"Решение из Safety Center после проверки переписки"})});await searchChats();if(chat?.conversation.user_id===userId){setChat({...chat,conversation:{...chat.conversation,user_status:"blocked"}});}setError("");}catch(r){setError(r instanceof Error?r.message:"Пользователь не заблокирован");}finally{setBusy(false);}};

  return <>
    <header className={styles.header}><div><h1>Безопасность</h1><p>Злоупотребления, подозрительная активность, поиск по перепискам и изоляция проблемных аккаунтов.</p></div></header>
    {error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}

    <section className={styles.section}>
      <h2>Safety Center: поиск по чатам</h2>
      <p>Введите до 20 фраз, по одной с новой строки или через запятую. Совпадение само по себе не блокирует пользователя: решение принимает администратор после просмотра контекста.</p>
      <div className={styles.filters}>
        <textarea value={terms} onChange={e=>setTerms(e.target.value)} placeholder={"system prompt\nignore previous instructions\nsql injection"} rows={4} style={{minWidth:420}}/>
        <button className={`${styles.button} ${styles.primary}`} disabled={busy||!terms.trim()} onClick={()=>void searchChats()}>Найти в чатах</button>
        <button className={styles.button} disabled={busy} onClick={()=>{setTerms("");setHits([]);setChat(null);}}>Очистить</button>
      </div>
      {hits.length>0&&<table className={styles.table}><thead><tr><th>Совпадение</th><th>Пользователь</th><th>Диалог</th><th>Сообщение</th><th>Дата</th><th></th></tr></thead><tbody>{hits.map(hit=><tr key={hit.message_id}><td>{hit.matched_terms.join(", ")||"—"}</td><td>{hit.username}<br/><small>{hit.email}</small><br/><span className={styles.pill}>{hit.user_status}</span></td><td>{hit.conversation_title}</td><td style={{maxWidth:520,whiteSpace:"pre-wrap"}}>{hit.content.length>500?`${hit.content.slice(0,500)}…`:hit.content}</td><td>{new Date(hit.created_at).toLocaleString("ru-RU")}</td><td><div className={styles.actions}><button className={styles.button} disabled={busy} onClick={()=>void openChat(hit.conversation_id)}>Вся переписка</button>{hit.user_status!=="blocked"&&<button className={`${styles.button} ${styles.danger}`} disabled={busy} onClick={()=>void blockUser(hit.user_id)}>Заблокировать</button>}</div></td></tr>)}</tbody></table>}
      {terms.trim()&&hits.length===0&&!busy&&<div className={styles.notice}>Совпадений нет.</div>}
    </section>

    {chat&&<section className={styles.section}>
      <h2>Переписка: {chat.conversation.title}</h2>
      <p>{chat.conversation.username} · {chat.conversation.email} · статус: {chat.conversation.user_status}</p>
      <div className={styles.actions}><button className={styles.button} onClick={()=>setChat(null)}>Закрыть</button>{chat.conversation.user_status!=="blocked"&&<button className={`${styles.button} ${styles.danger}`} disabled={busy} onClick={()=>void blockUser(chat.conversation.user_id)}>Заблокировать пользователя</button>}</div>
      <table className={styles.table}><thead><tr><th>Роль</th><th>Сообщение</th><th>Дата</th></tr></thead><tbody>{chat.messages.map(message=><tr key={message.id}><td>{message.role}</td><td style={{whiteSpace:"pre-wrap",maxWidth:850}}>{message.content}</td><td>{new Date(message.created_at).toLocaleString("ru-RU")}</td></tr>)}</tbody></table>
      {chat.truncated&&<div className={styles.notice}>Показаны первые 500 сообщений диалога.</div>}
    </section>}

    <section className={styles.section}><h2>Создать событие безопасности</h2><form className={styles.filters} onSubmit={create}><input name="category" placeholder="Категория" required/><select name="severity" defaultValue="warning"><option value="info">Информация</option><option value="warning">Предупреждение</option><option value="critical">Критическое</option></select><input name="summary" placeholder="Краткое описание" required style={{minWidth:260}}/><input name="user_id" placeholder="UUID пользователя (необязательно)"/><input name="ip_address" placeholder="IP (необязательно)"/><button className={`${styles.button} ${styles.primary}`} disabled={busy}>Создать</button></form></section>
    <section className={styles.section}><div className={styles.filters}><select value={status} onChange={e=>setStatus(e.target.value)}><option value="">Все статусы</option><option value="open">Открытые</option><option value="investigating">Расследуются</option><option value="resolved">Закрытые</option></select><button className={styles.button} onClick={()=>void load()}>Обновить</button></div><table className={styles.table}><thead><tr><th>Важность</th><th>Категория</th><th>Описание</th><th>Пользователь / IP</th><th>Статус</th><th>Дата</th><th></th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td className={item.severity==="critical"?styles.bad:item.severity==="warning"?styles.warn:""}>{severityLabel[item.severity]||item.severity}</td><td>{item.category}</td><td>{item.summary}</td><td>{item.user_id??"—"}<br/><small>{item.ip_address??""}</small></td><td><span className={styles.pill}>{statusLabel[item.status]||item.status}</span></td><td>{new Date(item.created_at).toLocaleString("ru-RU")}</td><td><div className={styles.actions}>{item.status==="open"&&<button className={styles.button} disabled={busy} onClick={()=>void act(item.id,"investigate")}>Начать расследование</button>}{item.status!=="resolved"&&<button className={styles.button} disabled={busy} onClick={()=>void act(item.id,"resolve")}>Закрыть</button>}{item.user_id&&item.status!=="resolved"&&<button className={`${styles.button} ${styles.danger}`} disabled={busy} onClick={()=>void act(item.id,"contain")}>Изолировать аккаунт</button>}</div></td></tr>)}</tbody></table></section>
  </>;
}
