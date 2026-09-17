"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {ApiError,api} from "../../lib/api";
import styles from "../commercial.module.css";

type Status={activated:boolean;completed_generations:number;has_paid:boolean;progress_percent:number;recommended_mode:string;starter_prompts:string[];steps:{key:string;title:string;done:boolean}[]};

export default function GettingStartedPage(){
  const [data,setData]=useState<Status|null>(null);const [error,setError]=useState("");
  useEffect(()=>{api<Status>("/auth/onboarding/").then(setData).catch((reason)=>setError(reason instanceof ApiError&&[401,403].includes(reason.status)?"Войдите в аккаунт, чтобы увидеть прогресс.":reason instanceof Error?reason.message:"Не удалось загрузить onboarding"));},[]);
  const choosePrompt=(prompt:string)=>{sessionStorage.setItem("starter-prompt",prompt);window.location.assign("/");};
  return <main className={styles.page}><div className={styles.shell}><header className={styles.top}><div className={styles.brand}>AI Workspace</div><nav className={styles.nav}><Link href="/">Чат</Link><Link href="/pricing">Стоимость</Link><Link href="/faq">FAQ</Link></nav></header><section className={styles.hero}><h1>Первый полезный ответ за несколько минут</h1><p>AUTO · Баланс подходит для старта: сервис сам подберёт модель, а вы увидите маршрутизацию и стоимость после ответа.</p></section>{error&&<div className={styles.notice}>{error} <Link href="/">Перейти ко входу</Link></div>}{data&&<><div className={styles.card}><h2>Прогресс: {data.progress_percent}%</h2><div className={styles.progress}><span style={{width:`${data.progress_percent}%`}}/></div><div className={styles.steps}>{data.steps.map(step=><div key={step.key} className={`${styles.step} ${step.done?styles.done:""}`}><span>{step.title}</span><b>{step.done?"Готово":"Следующий шаг"}</b></div>)}</div>{data.activated&&<p><b>Аккаунт активирован:</b> первый успешный AI-ответ уже получен.</p>}</div><section style={{marginTop:24}}><h2>Попробуйте один из запросов</h2>{data.starter_prompts.map(prompt=><button className={styles.prompt} key={prompt} onClick={()=>choosePrompt(prompt)}>{prompt} →</button>)}</section><div className={styles.actions}><Link className={styles.cta} href="/">Открыть чат</Link><Link className={styles.secondary} href="/pricing">Посмотреть оплату</Link></div></>}</div></main>;
}