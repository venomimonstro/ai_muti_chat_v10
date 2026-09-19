"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type Model={id:string;slug:string;enabled:boolean;current_version:string|null};
type Provider={id:string;slug:string;name:string;enabled:boolean;emergency_disabled:boolean;health_state:string;last_latency_ms:number|null;models:Model[]};
type ApiKey={id:string;label:string;masked:string;enabled:boolean;priority:number;health_state:string;last_error_code:string;last_latency_ms:number|null;balance_supported:boolean;balance_amount:string|null;balance_currency:string;balance_checked_at:string|null;last_checked_at:string|null};
type Credential={slug:string;name:string;adapter_type:string;api_base_url:string;credential_configured:boolean;credential_source:string;health_state:string;last_checked_at:string|null;last_latency_ms:number|null;keys:ApiKey[]};
type Price={input_rub_per_million:string;output_rub_per_million:string;example_1k_input_rub:string;example_1k_output_rub:string};
type DiscoveredModel={id:string;display_name:string;purpose:string;selected:boolean;price:Price|null};
type Discovery={provider:string;models:DiscoveredModel[]};

const healthLabel:Record<string,string>={healthy:"Работает",unknown:"Не проверен",degraded:"Ошибка",open:"Временно отключён",disabled:"Отключён"};

export default function ProvidersAdmin(){
 const[providers,setProviders]=useState<Provider[]>([]);
 const[credentials,setCredentials]=useState<Record<string,Credential>>({});
 const[keyDraft,setKeyDraft]=useState<Record<string,string>>({});
 const[labelDraft,setLabelDraft]=useState<Record<string,string>>({});
 const[models,setModels]=useState<Record<string,DiscoveredModel[]>>({});
 const[selected,setSelected]=useState<Record<string,Set<string>>>({});
 const[busy,setBusy]=useState("");
 const[error,setError]=useState("");
 const[notice,setNotice]=useState("");

 const load=async()=>{
  try{
   const p=await api<Provider[]>("/admin/providers/");
   const configs=await Promise.all(p.map(item=>api<Credential>(`/admin/providers/${item.slug}/credentials/`)));
   setProviders(p);
   setCredentials(Object.fromEntries(configs.map(item=>[item.slug,item])));
   setError("");
  }catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить AI-провайдеров");}
 };
 useEffect(()=>{void load();},[]);

 const addKey=async(slug:string)=>{
  const value=(keyDraft[slug]||"").trim();
  if(!value){setError("Вставьте API-ключ");return;}
  setBusy(`add:${slug}`);setError("");setNotice("");
  try{
   const result=await api<ApiKey>(`/admin/providers/${slug}/keys/`,{method:"POST",body:JSON.stringify({api_key:value,label:(labelDraft[slug]||"").trim()})});
   setKeyDraft(v=>({...v,[slug]:""}));setLabelDraft(v=>({...v,[slug]:""}));
   setNotice(result.health_state==="healthy"?"Ключ добавлен и проверен — подключение работает.":`Ключ сохранён, но проверка вернула ${result.last_error_code||"ошибку"}.`);
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"Не удалось добавить API-ключ");}
  finally{setBusy("");}
 };

 const keyAction=async(slug:string,key:ApiKey,action:"recheck"|"toggle"|"delete")=>{
  setBusy(`key:${key.id}`);setError("");setNotice("");
  try{
   if(action==="delete"){
    if(!window.confirm(`Удалить ${key.label}?`))return;
    await api(`/admin/providers/${slug}/keys/${key.id}/`,{method:"DELETE"});
   }else{
    await api(`/admin/providers/${slug}/keys/${key.id}/`,{method:"PATCH",body:JSON.stringify(action==="recheck"?{recheck:true}:{enabled:!key.enabled})});
   }
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"Операция с ключом не выполнена");}
  finally{setBusy("");}
 };

 const discover=async(slug:string)=>{
  setBusy(`discover:${slug}`);setError("");setNotice("");
  try{
   const result=await api<Discovery>(`/admin/providers/${slug}/discover-models/`);
   setModels(current=>({...current,[slug]:result.models}));
   setSelected(current=>({...current,[slug]:new Set(result.models.filter(x=>x.selected).map(x=>x.id))}));
   if(!result.models.length)setNotice("Провайдер не вернул доступных моделей для этого ключа.");
  }catch(reason){setError(reason instanceof Error?reason.message:"Не удалось получить модели");}
  finally{setBusy("");}
 };

 const toggleModelChoice=(slug:string,id:string)=>{
  setSelected(current=>{const next=new Set(current[slug]||[]);next.has(id)?next.delete(id):next.add(id);return {...current,[slug]:next};});
 };

 const saveModels=async(slug:string)=>{
  const ids=[...(selected[slug]||new Set<string>())];
  if(!ids.length){setError("Выберите хотя бы одну модель");return;}
  setBusy(`models:${slug}`);setError("");setNotice("");
  try{
   await api(`/admin/providers/${slug}/discover-models/`,{method:"POST",body:JSON.stringify({model_ids:ids})});
   setNotice(`Выбрано моделей: ${ids.length}. Теперь для коммерческого включения настройте их стоимость.`);
   await load();await discover(slug);
  }catch(reason){setError(reason instanceof Error?reason.message:"Не удалось сохранить выбранные модели");}
  finally{setBusy("");}
 };

 const toggleProvider=async(provider:Provider)=>{
  setBusy(`provider:${provider.id}`);setError("");
  try{await api("/admin/providers/bulk-action/",{method:"POST",body:JSON.stringify({target:"providers",action:provider.enabled?"disable":"enable",ids:[provider.id]})});await load();}
  catch(reason){setError(reason instanceof Error?reason.message:"Не удалось изменить состояние провайдера");}
  finally{setBusy("");}
 };

 const priceText=(price:Price|null)=>price?`${price.input_rub_per_million} ₽ вход / ${price.output_rub_per_million} ₽ выход за 1 млн токенов`:"Точная стоимость ещё не настроена";

 return <>
  <header className={styles.header}><div><h1>AI-провайдеры</h1><p>Простое подключение: добавьте ключ, выберите модели из найденного списка и включите нужные.</p></div><button className={styles.button} disabled={!!busy} onClick={()=>void load()}>Обновить</button></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  <div className={styles.notice}><b>Как подключить:</b> 1. Вставьте API-ключ → 2. система сама проверит его → 3. нажмите «Показать модели» → 4. отметьте одну или несколько моделей → 5. сохраните выбор.</div>
  {providers.map(provider=>{
   const credential=credentials[provider.slug];
   const keys=credential?.keys||[];
   const discovered=models[provider.slug]||[];
   const healthyKeys=keys.filter(k=>k.enabled&&k.health_state==="healthy").length;
   return <section className={styles.section} key={provider.id}>
    <div className={styles.header}><div><h2>{provider.name}</h2><p>{healthyKeys?`Рабочих ключей: ${healthyKeys} из ${keys.length}`:keys.length?"Нет рабочего ключа":"Ключи ещё не добавлены"}</p></div><div className={styles.actions}><strong className={provider.health_state==="healthy"?styles.good:styles.warn}>{healthLabel[provider.health_state]||provider.health_state}</strong><button className={styles.button} disabled={!!busy} onClick={()=>void toggleProvider(provider)}>{provider.enabled?"Выключить":"Включить"}</button></div></div>

    <h3>API-ключи</h3>
    <div className={styles.filters}>
     <input value={labelDraft[provider.slug]||""} onChange={e=>setLabelDraft(v=>({...v,[provider.slug]:e.target.value}))} placeholder="Название ключа, например Основной"/>
     <input type="password" autoComplete="off" value={keyDraft[provider.slug]||""} onChange={e=>setKeyDraft(v=>({...v,[provider.slug]:e.target.value}))} placeholder="Вставьте API-ключ"/>
     <button className={`${styles.button} ${styles.primary}`} disabled={!!busy} onClick={()=>void addKey(provider.slug)}>{busy===`add:${provider.slug}`?"Проверяем…":"Подключить ключ"}</button>
    </div>

    {keys.length>0&&<table className={styles.table}><thead><tr><th>Ключ</th><th>Статус</th><th>Баланс</th><th>Задержка</th><th></th></tr></thead><tbody>{keys.map(key=><tr key={key.id}><td><b>{key.label}</b><br/><small>{key.masked}</small></td><td className={key.health_state==="healthy"?styles.good:styles.warn}>{healthLabel[key.health_state]||key.health_state}{key.last_error_code&&<><br/><small>{key.last_error_code}</small></>}</td><td>{key.balance_supported&&key.balance_amount!=null?<b>{key.balance_amount} {key.balance_currency}</b>:<small>Провайдер не отдаёт баланс через API</small>}</td><td>{key.last_latency_ms!=null?`${key.last_latency_ms} мс`:"—"}</td><td><div className={styles.actions}><button className={styles.button} disabled={!!busy} onClick={()=>void keyAction(provider.slug,key,"recheck")}>Перепроверить</button><button className={styles.button} disabled={!!busy} onClick={()=>void keyAction(provider.slug,key,"toggle")}>{key.enabled?"Выключить":"Включить"}</button><button className={`${styles.button} ${styles.danger}`} disabled={!!busy} onClick={()=>void keyAction(provider.slug,key,"delete")}>Удалить</button></div></td></tr>)}</tbody></table>}

    <div className={styles.actions} style={{marginTop:16}}><button className={`${styles.button} ${styles.primary}`} disabled={!!busy||(!healthyKeys&&!credential?.credential_configured)} onClick={()=>void discover(provider.slug)}>{busy===`discover:${provider.slug}`?"Получаем модели…":"Показать доступные модели"}</button></div>

    {discovered.length>0&&<><h3>Доступные модели</h3><p>Система получила этот список непосредственно у {provider.name}. Можно выбрать несколько моделей.</p><table className={styles.table}><thead><tr><th></th><th>Модель</th><th>Для чего лучше</th><th>Стоимость</th></tr></thead><tbody>{discovered.map(model=><tr key={model.id}><td><input type="checkbox" checked={selected[provider.slug]?.has(model.id)||false} onChange={()=>toggleModelChoice(provider.slug,model.id)}/></td><td><b>{model.display_name}</b><br/><small>{model.id}</small>{model.selected&&<><br/><span className={styles.good}>уже добавлена</span></>}</td><td>{model.purpose}</td><td>{priceText(model.price)}{model.price&&<><br/><small>Пример 1000 токенов: вход {model.price.example_1k_input_rub} ₽, выход {model.price.example_1k_output_rub} ₽</small></>}</td></tr>)}</tbody></table><div className={styles.actions}><button className={`${styles.button} ${styles.primary}`} disabled={!!busy} onClick={()=>void saveModels(provider.slug)}>Сохранить выбранные модели</button></div></>}
   </section>;
  })}
 </>;
}
