"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "../../lib/api";
import styles from "../commercial.module.css";

export default function VerifyEmailPage(){const [state,setState]=useState<"working"|"done"|"error">("working");const [message,setMessage]=useState("Проверяем ссылку…");useEffect(()=>{const token=new URLSearchParams(window.location.search).get("token")||"";if(!token){setState("error");setMessage("В ссылке отсутствует токен подтверждения.");return;}api<{verified:boolean}>("/auth/verify-email/confirm/",{method:"POST",body:JSON.stringify({token})}).then(()=>{setState("done");setMessage("Email подтверждён. Промо-баланс активирован, если он предусмотрен текущими условиями.");}).catch((reason)=>{setState("error");setMessage(reason instanceof Error?reason.message:"Ссылка недействительна или истекла");});},[]);return <main className={styles.page}><div className={styles.shell}><section className={styles.hero}><h1>{state==="done"?"Email подтверждён":state==="error"?"Не удалось подтвердить email":"Подтверждаем email"}</h1><p>{message}</p><div className={styles.actions}><Link className={styles.cta} href="/">Открыть AI Workspace</Link>{state==="error"&&<Link className={styles.secondary} href="/getting-started">Вернуться к началу работы</Link>}</div></section></div></main>}