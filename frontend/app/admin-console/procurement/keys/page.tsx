"use client";

import {useEffect,useState} from "react";
import {api} from "../../../../lib/api";
import styles from "../../admin.module.css";

type LedgerKey={
  id:string;provider:string;provider_name:string;label:string;masked:string;enabled:boolean;health_state:string;
  provider_balance_amount:string|null;provider_balance_currency:string;provider_balance_supported:boolean;
  account_id:string|null;account_currency:string|null;ledger_available_native:string|null;ledger_spent_native:string|null;
  ledger_reserved_native?:string|null;is_default:boolean;
};
type LedgerData={summary:Record<string,unknown>;keys:LedgerKey[];accounts:unknown[];purchases:unknown[]};

const num=(v:string|number|null|undefined,d=6)=>Number(v??0).toFixed(d).replace(".",",");
const health:Record<string,string>={healthy:"Работает",unknown:"Не проверен",degraded:"Ошибка",disabled:"Отключён"};

export default function ProcurementKeysPage(){
  const[data,setData]=useState<LedgerData|null>(null);
  const[busy,setBusy]=useState(false);
  const[error,setError]=useState("");
  const[notice,setNotice]=useState("");

  const load=async()=>{setBusy(true);try{setData(await api<LedgerData>("/admin/procurement/ledger/"));setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить ключи")}finally{setBusy(false)}};
  useEffect(()=>{void load()},[]);

  const makeDefault=async(accountId:string)=>{setBusy(true);setError("");setNotice("");try{await api("/admin/procurement/ledger/",{method:"POST",body:JSON.stringify({action:"set_default",account_id:accountId})});setNotice("Ключ назначен основным закупочным ключом провайдера.");await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось назначить основной ключ")}finally{setBusy(false)}};

  return <>
    <header className={styles.header}><div><h1>API-ключи и остатки</h1><p>Отдельный реестр ключей: технический статус, наш закупочный остаток и фактический balance провайдера, если его API это поддерживает.</p></div><button className={styles.button} disabled={busy} onClick={()=>void load()}>Обновить</button></header>
    {notice&&<div className={styles.notice}>{notice}</div>}
    {error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
    {data&&<section className={styles.section}>
      {data.keys.length===0?<p>API-ключи ещё не добавлены. Добавьте их в разделе «AI-провайдеры».</p>:<table className={styles.table}>
        <thead><tr><th>Провайдер / ключ</th><th>Статус API</th><th>Наш закупочный остаток</th><th>Баланс провайдера</th><th>Роль</th><th></th></tr></thead>
        <tbody>{data.keys.map(k=><tr key={k.id}>
          <td><b>{k.provider_name} · {k.label}</b><br/><small>{k.masked}</small></td>
          <td className={k.health_state==="healthy"?styles.good:styles.warn}>{health[k.health_state]||k.health_state}</td>
          <td>{k.account_id?<><b>{num(k.ledger_available_native)} {k.account_currency}</b><br/><small>израсходовано {num(k.ledger_spent_native)} {k.account_currency}</small>{Number(k.ledger_reserved_native??0)>0&&<><br/><small>зарезервировано {num(k.ledger_reserved_native)} {k.account_currency}</small></>}</>:<><b>Закупок ещё не было</b><br/><small>закупочного счёта нет</small></>}</td>
          <td>{k.provider_balance_supported&&k.provider_balance_amount!=null?<><b>{k.provider_balance_amount} {k.provider_balance_currency}</b><br/><small>получено напрямую от провайдера</small></>:<><span>Не предоставляется</span><br/><small>этот API-ключ не раскрывает provider balance</small></>}</td>
          <td>{k.is_default?<span className={styles.good}>Основной закупочный ключ</span>:k.account_id?<span>Учёт ведётся</span>:<span className={styles.warn}>Нет закупочного счёта</span>}</td>
          <td>{k.account_id&&!k.is_default&&<button className={styles.button} disabled={busy} onClick={()=>void makeDefault(k.account_id!)}>Сделать основным</button>}</td>
        </tr>)}</tbody>
      </table>}
    </section>}
  </>;
}
