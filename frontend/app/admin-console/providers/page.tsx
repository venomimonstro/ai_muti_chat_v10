"use client";

import Link from "next/link";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type Model={id:string;slug:string;enabled:boolean;current_version:string|null};
type Provider={id:string;slug:string;name:string;enabled:boolean;emergency_disabled:boolean;health_state:string;last_latency_ms:number|null;models:Model[]};
type Credential={slug:string;name:string;adapter_type:string;api_base_url:string;credential_env:string;credential_configured:boolean;credential_source:"database"|"environment"|"none";health_state:string;last_checked_at:string|null;last_latency_ms:number|null};
type SetupModel={slug:string;enabled:boolean;upstream_model:string;has_active_version:boolean;has_active_price:boolean};
type SetupProvider={slug:string;models:SetupModel[]};
type Setup={providers:SetupProvider[]};
type HealthResult={healthy:boolean;latency_ms:number|null;error_code:string};

const healthLabel:Record<string,string>={healthy:"Работает",unknown:"Не проверен",degraded:"Ошибка связи",open:"Автоматически отключён",disabled:"Отключён"};
const sourceLabel:Record<string,string>={database:"сохранён в панели",environment:"из .env сервера",none:"не настроен"};

export default function ProvidersAdmin(){
 const[providers,setProviders]=useState<Provider[]>([]);
 const[credentials,setCredentials]=useState<Record<string,Credential>>({});
 const[setup,setSetup]=useState<Record<string,SetupProvider>>({});
 const[keyDraft,setKeyDraft]=useState<Record<string,string>>({});
 const[baseDraft,setBaseDraft]=useState<Record<string,string>>({});
 const[modelDraft,setModelDraft]=useState<Record<string,string>>({});
 const[busy,setBusy]=useState("");
 const[error,setError]=useState("");
 const[notice,setNotice]=useState("");

 const load=async()=>{
  try{
   const[p,s]=await Promise.all([api<Provider[]>("/admin/providers/"),api<Setup>("/admin/commercial-setup/")]);
   const configs=await Promise.all(p.map(item=>api<Credential>(`/admin/providers/${item.slug}/credentials/`)));
   setProviders(p);
   setCredentials(Object.fromEntries(configs.map(item=>[item.slug,item])));
   setSetup(Object.fromEntries(s.providers.map(item=>[item.slug,item])));
   setBaseDraft(current=>{const next={...current};for(const item of configs)if(next[item.slug]===undefined)next[item.slug]=item.api_base_url||"";return next;});
   setModelDraft(current=>{const next={...current};for(const provider of s.providers)for(const model of provider.models)if(next[model.slug]===undefined)next[model.slug]=model.upstream_model||"";return next;});
   setError("");
  }catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить провайдеров");}
 };
 useEffect(()=>{void load();},[]);

 const configuredCount=useMemo(()=>Object.values(credentials).filter(item=>item.credential_configured).length,[credentials]);
 const healthyCount=useMemo(()=>providers.filter(item=>item.health_state==="healthy").length,[providers]);

 const saveCredential=async(slug:string)=>{
  setBusy(`credential:${slug}`);setError("");setNotice("");
  try{
   const result=await api<Credential>(`/admin/providers/${slug}/credentials/`,{method:"PATCH",body:JSON.stringify({api_key:(keyDraft[slug]||"").trim(),api_base_url:(baseDraft[slug]||"").trim()})});
   setCredentials(current=>({...current,[slug]:result}));
   setKeyDraft(current=>({...current,[slug]:""}));
   setNotice(`${result.name}: настройки сохранены. Теперь нажмите «Проверить подключение».`);
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"Не удалось сохранить API-ключ");}
  finally{setBusy("");}
 };

 const clearCredential=async(slug:string)=>{
  if(!window.confirm("Удалить сохранённый API-ключ этого провайдера?"))return;
  setBusy(`credential:${slug}`);setError("");setNotice("");
  try{await api(`/admin/providers/${slug}/credentials/`,{method:"PATCH",body:JSON.stringify({clear_api_key:true})});setKeyDraft(current=>({...current,[slug]:""}));setNotice("API-ключ удалён.");await load();}
  catch(reason){setError(reason instanceof Error?reason.message:"Не удалось удалить API-ключ");}
  finally{setBusy("");}
 };

 const testConnection=async(slug:string)=>{
  setBusy(`health:${slug}`);setError("");setNotice("");
  try{
   const result=await api<HealthResult>(`/admin/commercial-setup/providers/${slug}/health/`,{method:"POST",body:"{}"});
   if(result.healthy)setNotice(`${credentials[slug]?.name||slug}: подключение работает${result.latency_ms!=null?` · ${result.latency_ms} мс`:""}.`);
   else setError(`${credentials[slug]?.name||slug}: API недоступен. Код: ${result.error_code||"provider_error"}. Проверьте ключ и API URL.`);
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"Не удалось выполнить проверку API");}
  finally{setBusy("");}
 };

 const saveModel=async(providerSlug:string,model:Model)=>{
  const upstream=(modelDraft[model.slug]||"").trim();
  if(!upstream){setError("Укажите точный ID модели у провайдера");return;}
  setBusy(`model:${model.id}`);setError("");setNotice("");
  try{await api(`/admin/providers/${providerSlug}/models/${model.id}/`,{method:"PATCH",body:JSON.stringify({upstream_model:upstream})});setNotice(`${model.slug}: ID модели сохранён и создана активная версия.`);await load();}
  catch(reason){setError(reason instanceof Error?reason.message:"Не удалось сохранить модель");}
  finally{setBusy("");}
 };

 const toggle=async(target:"providers"|"models",id:string,enable:boolean)=>{
  setBusy(`${target}:${id}`);setError("");setNotice("");
  try{await api("/admin/providers/bulk-action/",{method:"POST",body:JSON.stringify({target,action:enable?"enable":"disable",ids:[id]})});setNotice(enable?"Включено.":"Выключено.");await load();}
  catch(reason){setError(reason instanceof Error?reason.message:"Не удалось изменить состояние");}
  finally{setBusy("");}
 };

 return <>
  <header className={styles.header}><div><h1>AI-провайдеры</h1><p>Подключение API, проверка ключей и настройка реальных ID моделей. Финансовые настройки вынесены отдельно.</p></div><button className={styles.button} disabled={!!busy} onClick={()=>void load()}>Обновить</button></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  <section className={styles.grid}>
   <div className={styles.card}><small>API-ключ настроен</small><strong>{configuredCount} / {providers.length}</strong></div>
   <div className={styles.card}><small>Связь проверена</small><strong className={healthyCount?styles.good:styles.warn}>{healthyCount} / {providers.length}</strong></div>
  </section>
  <div className={styles.notice}>Порядок подключения: <b>1.</b> вставьте API-ключ → <b>2.</b> сохраните → <b>3.</b> проверьте подключение → <b>4.</b> укажите точный ID модели → <b>5.</b> настройте себестоимость в <Link href="/admin-console/finance">финансах</Link> → <b>6.</b> включите модель.</div>
  {providers.map(provider=>{
   const credential=credentials[provider.slug];
   const setupProvider=setup[provider.slug];
   return <section className={styles.section} key={provider.id}>
    <div className={styles.header}><div><h2>{provider.name}</h2><p>{provider.slug} · {credential?.adapter_type||"—"}</p></div><div><b className={provider.health_state==="healthy"?styles.good:styles.warn}>{healthLabel[provider.health_state]||provider.health_state}</b>{provider.last_latency_ms!=null&&<span> · {provider.last_latency_ms} мс</span>}</div></div>
    <div className={styles.grid}>
     <div className={styles.card}><small>API-ключ</small><strong className={credential?.credential_configured?styles.good:styles.warn}>{credential?.credential_configured?"Настроен":"Не настроен"}</strong><p>{credential?sourceLabel[credential.credential_source]:"—"}</p></div>
     <div className={styles.card}><small>Провайдер</small><strong>{provider.enabled?"Включён":"Выключен"}</strong><p>{provider.emergency_disabled?"Аварийно отключён":"Без аварийной блокировки"}</p></div>
    </div>
    <h3>Подключение API</h3>
    <div className={styles.filters}>
     <input type="password" autoComplete="off" value={keyDraft[provider.slug]||""} onChange={e=>setKeyDraft(current=>({...current,[provider.slug]:e.target.value}))} placeholder={credential?.credential_configured?"Новый ключ (пусто = оставить текущий)":"Вставьте API-ключ"}/>
     <input value={baseDraft[provider.slug]??credential?.api_base_url??""} onChange={e=>setBaseDraft(current=>({...current,[provider.slug]:e.target.value}))} placeholder="API base URL"/>
     <button className={`${styles.button} ${styles.primary}`} disabled={!!busy} onClick={()=>void saveCredential(provider.slug)}>Сохранить</button>
     <button className={styles.button} disabled={!!busy||!credential?.credential_configured} onClick={()=>void testConnection(provider.slug)}>{busy===`health:${provider.slug}`?"Проверяем…":"Проверить подключение"}</button>
     {credential?.credential_source==="database"&&<button className={`${styles.button} ${styles.danger}`} disabled={!!busy} onClick={()=>void clearCredential(provider.slug)}>Удалить ключ</button>}
    </div>
    <h3>Модели</h3>
    {(provider.models.length===0)&&<p>Для этого провайдера пока нет модели. Запустите bootstrap каталога или добавьте модель через конфигурацию проекта.</p>}
    {provider.models.map(model=>{
      const setupModel=setupProvider?.models.find(item=>item.slug===model.slug);
      return <div key={model.id} className={styles.card} style={{marginBottom:12}}>
       <div className={styles.header}><div><b>{model.slug}</b><p>Версия: {model.current_version||"не назначена"} · Цена: {setupModel?.has_active_price?"настроена":"не настроена"}</p></div><strong className={model.enabled?styles.good:styles.warn}>{model.enabled?"Включена":"Выключена"}</strong></div>
       <div className={styles.filters}>
        <input value={modelDraft[model.slug]??setupModel?.upstream_model??""} onChange={e=>setModelDraft(current=>({...current,[model.slug]:e.target.value}))} placeholder="Точный model ID, например deepseek-chat"/>
        <button className={styles.button} disabled={!!busy} onClick={()=>void saveModel(provider.slug,model)}>Сохранить model ID</button>
        <button className={styles.button} disabled={!!busy} onClick={()=>void toggle("models",model.id,!model.enabled)}>{model.enabled?"Выключить модель":"Включить модель"}</button>
       </div>
      </div>;
    })}
    <div className={styles.actions}><button className={styles.button} disabled={!!busy} onClick={()=>void toggle("providers",provider.id,!provider.enabled)}>{provider.enabled?"Выключить провайдера":"Включить провайдера"}</button></div>
   </section>;
  })}
 </>;
}
