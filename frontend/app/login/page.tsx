"use client";

import Link from "next/link";
import {FormEvent,useState} from "react";
import {api,ensureCsrf,setTestUserMode} from "../../lib/api";
import styles from "../auth.module.css";

type LoginUser={id?:string;role?:string;status?:string;is_staff?:boolean;is_superuser?:boolean;test_user_mode?:boolean;test_user_id?:string};

function isPlatformAdmin(user:LoginUser){
  return user.role==="platform_admin"||user.is_staff===true||user.is_superuser===true;
}

export default function LoginPage(){
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [show,setShow]=useState(false);

  const submit=async(e:FormEvent<HTMLFormElement>)=>{
    e.preventDefault();
    setBusy(true);
    setError("");
    const f=new FormData(e.currentTarget);
    try{
      await ensureCsrf();
      const result=await api<LoginUser>("/auth/login/",{
        method:"POST",
        body:JSON.stringify({username:f.get("username"),password:f.get("password")}),
      });

      // If a platform-admin session already exists in this browser, backend
      // deliberately keeps it untouched and authenticates this tab as the
      // requested client via X-Test-User. sessionStorage is tab-scoped, so the
      // admin console in neighbouring tabs remains the real administrator.
      if(result.test_user_mode&&result.test_user_id){
        setTestUserMode(result.test_user_id);
        window.location.replace("/app");
        return;
      }

      const current=await api<LoginUser>("/auth/me/",{signal:AbortSignal.timeout(5000)});
      if(current.status!=="active") throw new Error("Аккаунт неактивен");
      window.location.replace(isPlatformAdmin(current)?"/admin-console":"/app");
    }catch(r){
      setError(r instanceof Error?r.message:"Не удалось войти");
      setBusy(false);
    }
  };

  return <main className={styles.page}><div className={styles.shell}><div className={styles.brand}><Link href="/">AI Workspace</Link></div><section className={styles.card}><p className={styles.eyebrow}>ВХОД В АККАУНТ</p><h1>С возвращением</h1><p className={styles.muted}>Продолжите работу с чатами, проектами, файлами и единым балансом.</p><form className={styles.form} onSubmit={submit}><label>Логин<div className={styles.inputWrap}><input name="username" autoComplete="username" required autoFocus/></div></label><label>Пароль<div className={styles.inputWrap}><input name="password" type={show?"text":"password"} autoComplete="current-password" required/><button className={styles.show} type="button" onClick={()=>setShow(v=>!v)}>{show?"Скрыть":"Показать"}</button></div></label>{error&&<div className={styles.error} role="alert">{error}</div>}<button className={styles.primary} disabled={busy}>{busy?"Проверяем вход…":"Войти"}</button></form><div className={styles.links}><Link href="/forgot-password">Забыли пароль?</Link><Link href="/register">Создать аккаунт</Link></div></section></div></main>;
}
