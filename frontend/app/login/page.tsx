"use client";

import Link from "next/link";
import {FormEvent,useState} from "react";
import {useRouter} from "next/navigation";
import {api,ensureCsrf} from "../../lib/api";
import styles from "../auth.module.css";

type LoginUser={role?:string;status?:string};

export default function LoginPage(){
  const router=useRouter();
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
      const user=await api<LoginUser>("/auth/login/",{
        method:"POST",
        body:JSON.stringify({username:f.get("username"),password:f.get("password")}),
      });
      const destination=user.role==="platform_admin"?"/admin-console":"/app";
      router.replace(destination);
      router.refresh();
    }catch(r){
      setError(r instanceof Error?r.message:"Не удалось войти");
    }finally{
      setBusy(false);
    }
  };

  return <main className={styles.page}><div className={styles.shell}><div className={styles.brand}><Link href="/">AI Workspace</Link></div><section className={styles.card}><p className={styles.eyebrow}>ВХОД В АККАУНТ</p><h1>С возвращением</h1><p className={styles.muted}>Продолжите работу с чатами, проектами, файлами и единым балансом.</p><form className={styles.form} onSubmit={submit}><label>Логин<div className={styles.inputWrap}><input name="username" autoComplete="username" required autoFocus/></div></label><label>Пароль<div className={styles.inputWrap}><input name="password" type={show?"text":"password"} autoComplete="current-password" required/><button className={styles.show} type="button" onClick={()=>setShow(v=>!v)}>{show?"Скрыть":"Показать"}</button></div></label>{error&&<div className={styles.error} role="alert">{error}</div>}<button className={styles.primary} disabled={busy}>{busy?"Входим…":"Войти"}</button></form><div className={styles.links}><Link href="/forgot-password">Забыли пароль?</Link><Link href="/register">Создать аккаунт</Link></div></section></div></main>;
}
