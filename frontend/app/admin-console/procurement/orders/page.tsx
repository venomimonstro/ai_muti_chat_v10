"use client";

import {useEffect,useState} from "react";
import {api} from "../../../../lib/api";
import styles from "../../admin.module.css";

type LedgerKey={id:string;provider_name:string;label:string;masked:string;account_id:string|null;account_currency:string|null;is_default:boolean};
type Purchase={
 id:string;document_number:string;state:"active"|"cancelled"|"deleted";provider_name:string;account_id:string;api_key_id:string|null;api_key_label:string;api_key_masked:string;
 credit_native:string;credit_currency:string;consumed_native:string;remaining_native:string;payment_amount:string|null;payment_currency:string;payment_fx_rate_rub:string|null;market_fx_rate_rub:string|null;
 base_cost_rub:string;fees_rub:string;total_cash_outlay_rub:string;effective_cost_rub_per_native:string;realized_revenue_rub:string;realized_profit_rub:string;realized_margin_percent:string;
 operations_count:number;reference:string;purchased_at:string;editable:boolean;cancellable:boolean;deletable:boolean;
};
type LedgerData={summary:{purchase_documents:number;cash_outlay_rub:string;fees_rub:string;realized_revenue_rub:string;realized_profit_rub:string;realized_margin_percent:string};keys:LedgerKey[];purchases:Purchase[]};
type Draft={api_key_id:string;credit_native:string;credit_currency:string;payment_amount:string;payment_currency:string;payment_fx_rate_rub:string;fees_rub:string;market_fx_rate_rub:string;purchased_at:string;reference:string};

const emptyDraft=():Draft=>({api_key_id:"",credit_native:"",credit_currency:"USD",payment_amount:"",payment_currency:"RUB",payment_fx_rate_rub:"",fees_rub:"0",market_fx_rate_rub:"",purchased_at:new Date().toISOString().slice(0,10),reference:""});
const money=(v:string|number|null|undefined)=>`${Number(v??0).toFixed(2).replace(".",",")} ₽`;
const num=(v:string|number|null|undefined,d=6)=>Number(v??0).toFixed(d).replace(".",",");
const stateLabel={active:"Активен",cancelled:"Отменён",deleted:"Удалён"};

export default function ProcurementOrdersPage(){
 const[data,setData]=useState<LedgerData|null>(null);
 const[draft,setDraft]=useState<Draft>(emptyDraft());
 const[editing,setEditing]=useState<Purchase|null>(null);
 const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[notice,setNotice]=useState("");

 const load=async()=>{setBusy(true);try{const result=await api<LedgerData>("/admin/procurement/ledger/");setData(result);setDraft(v=>({...v,api_key_id:v.api_key_id||result.keys[0]?.id||""}));setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить закупочные ордера")}finally{setBusy(false)}};
 useEffect(()=>{void load()},[]);
 const post=async<T=unknown>(payload:Record<string,unknown>)=>api<T>("/admin/procurement/ledger/",{method:"POST",body:JSON.stringify(payload)});

 const reset=()=>{setEditing(null);setDraft(v=>({...emptyDraft(),api_key_id:v.api_key_id}));};
 const save=async()=>{if(!draft.credit_native||!draft.payment_amount||(!editing&&!draft.api_key_id)){setError("Заполните API-ключ, номинал и фактическую оплату");return;}setBusy(true);setError("");setNotice("");try{
   if(editing){const p=await post<Purchase>({action:"edit_purchase",purchase_id:editing.id,...draft,base_cost_rub:"",payment_fx_rate_rub:draft.payment_currency==="RUB"?"":draft.payment_fx_rate_rub});setNotice(`Ордер ${p.document_number} изменён.`)}
   else{const p=await post<Purchase>({action:"purchase_key",...draft,base_cost_rub:"",payment_fx_rate_rub:draft.payment_currency==="RUB"?"":draft.payment_fx_rate_rub});setNotice(`Ордер ${p.document_number} создан. На ключ зачислено ${p.credit_native} ${p.credit_currency}.`)}
   reset();await load();
 }catch(e){setError(e instanceof Error?e.message:"Не удалось сохранить ордер")}finally{setBusy(false)}};

 const startEdit=(p:Purchase)=>{setEditing(p);setDraft({api_key_id:p.api_key_id||"",credit_native:p.credit_native,credit_currency:p.credit_currency,payment_amount:p.payment_amount||"",payment_currency:p.payment_currency,payment_fx_rate_rub:p.payment_fx_rate_rub||"",fees_rub:p.fees_rub,market_fx_rate_rub:p.market_fx_rate_rub||"",purchased_at:p.purchased_at.slice(0,10),reference:p.reference||""});window.scrollTo({top:0,behavior:"smooth"});};
 const orderAction=async(p:Purchase,action:"cancel_purchase"|"delete_purchase")=>{const title=action==="cancel_purchase"?"Отменить":"Удалить";if(!window.confirm(`${title} закупочный ордер ${p.document_number}?`))return;setBusy(true);setError("");setNotice("");try{await post({action,purchase_id:p.id});setNotice(action==="cancel_purchase"?`Ордер ${p.document_number} отменён.`:`Ордер ${p.document_number} удалён из рабочего реестра.`);if(editing?.id===p.id)reset();await load()}catch(e){setError(e instanceof Error?e.message:"Операция не выполнена")}finally{setBusy(false)}};

 return <>
  <header className={styles.header}><div><h1>Закупочные ордера API</h1><p>Отдельный журнал закупок: номинал, фактическая оплата, курс, комиссии, FIFO-остаток, продажи и прибыль.</p></div><button className={styles.button} disabled={busy} onClick={()=>void load()}>Обновить</button></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  {data&&<>
   <section className={styles.grid}>
    <div className={styles.card}><small>Активных ордеров</small><strong>{data.summary.purchase_documents}</strong></div>
    <div className={styles.card}><small>Фактически вложено</small><strong>{money(data.summary.cash_outlay_rub)}</strong></div>
    <div className={styles.card}><small>Комиссии</small><strong>{money(data.summary.fees_rub)}</strong></div>
    <div className={styles.card}><small>Выручка</small><strong>{money(data.summary.realized_revenue_rub)}</strong></div>
    <div className={styles.card}><small>Реализованная прибыль</small><strong>{money(data.summary.realized_profit_rub)}</strong><small>{num(data.summary.realized_margin_percent,2)}%</small></div>
   </section>

   <section className={styles.section}>
    <div className={styles.header}><div><h2>{editing?`Изменить ${editing.document_number}`:"Новый закупочный ордер"}</h2><p>{editing?"Изменение разрешено только пока партия не участвовала в фактических списаниях.":"Оформите закупку для конкретного API-ключа."}</p></div>{editing&&<button className={styles.button} onClick={reset}>Отменить редактирование</button>}</div>
    <div className={styles.filters}>
     <label>API-ключ<select disabled={!!editing} value={draft.api_key_id} onChange={e=>setDraft(v=>({...v,api_key_id:e.target.value}))}><option value="">Выберите ключ</option>{data.keys.map(k=><option key={k.id} value={k.id}>{k.provider_name} · {k.label} · {k.masked}</option>)}</select></label>
     <label>Номинал на балансе<input inputMode="decimal" value={draft.credit_native} onChange={e=>setDraft(v=>({...v,credit_native:e.target.value}))} placeholder="10"/></label>
     <label>Валюта баланса<select disabled={!!editing} value={draft.credit_currency} onChange={e=>setDraft(v=>({...v,credit_currency:e.target.value}))}><option>USD</option><option>EUR</option><option>RUB</option></select></label>
     <label>Фактически оплачено<input inputMode="decimal" value={draft.payment_amount} onChange={e=>setDraft(v=>({...v,payment_amount:e.target.value}))} placeholder="15"/></label>
     <label>Валюта оплаты<select value={draft.payment_currency} onChange={e=>setDraft(v=>({...v,payment_currency:e.target.value}))}><option>RUB</option><option>USD</option><option>EUR</option></select></label>
     {draft.payment_currency!=="RUB"&&<label>Фактический курс ₽ / 1 {draft.payment_currency}<input inputMode="decimal" value={draft.payment_fx_rate_rub} onChange={e=>setDraft(v=>({...v,payment_fx_rate_rub:e.target.value}))} placeholder="95"/></label>}
     <label>Комиссии отдельно, ₽<input inputMode="decimal" value={draft.fees_rub} onChange={e=>setDraft(v=>({...v,fees_rub:e.target.value}))}/></label>
     <label>Рыночный курс, ₽<input inputMode="decimal" value={draft.market_fx_rate_rub} onChange={e=>setDraft(v=>({...v,market_fx_rate_rub:e.target.value}))} placeholder="необязательно"/></label>
     <label>Дата<input type="date" value={draft.purchased_at} onChange={e=>setDraft(v=>({...v,purchased_at:e.target.value}))}/></label>
     <label>Комментарий / чек<input value={draft.reference} onChange={e=>setDraft(v=>({...v,reference:e.target.value}))} placeholder="номер чека, обменник, комментарий"/></label>
     <button className={`${styles.button} ${styles.primary}`} disabled={busy||data.keys.length===0} onClick={()=>void save()}>{editing?"Сохранить изменения":"Провести закупку"}</button>
    </div>
   </section>

   <section className={styles.section}>
    <h2>Журнал закупочных ордеров</h2>
    {data.purchases.length===0?<p>Закупок пока нет.</p>:<table className={styles.table}><thead><tr><th>Документ</th><th>Статус</th><th>Ключ</th><th>Куплено</th><th>Оплата / курс</th><th>Себестоимость</th><th>FIFO</th><th>Продажи / прибыль</th><th>Действия</th></tr></thead><tbody>{data.purchases.map(p=><tr key={p.id}>
      <td><b>{p.document_number}</b><br/><small>{new Date(p.purchased_at).toLocaleDateString("ru-RU")}</small>{p.reference&&<><br/><small>{p.reference}</small></>}</td>
      <td className={p.state==="active"?styles.good:styles.warn}><b>{stateLabel[p.state]}</b></td>
      <td><b>{p.provider_name}</b><br/>{p.api_key_label}<br/><small>{p.api_key_masked}</small></td>
      <td><b>{num(p.credit_native)} {p.credit_currency}</b></td>
      <td>{p.payment_amount!=null?<><b>{num(p.payment_amount)} {p.payment_currency}</b>{p.payment_fx_rate_rub&&<><br/><small>курс {num(p.payment_fx_rate_rub,4)} ₽</small></>}{Number(p.fees_rub)>0&&<><br/><small>комиссия {money(p.fees_rub)}</small></>}</>:"историческая запись"}</td>
      <td><b>{money(p.total_cash_outlay_rub)}</b><br/><small>{money(p.effective_cost_rub_per_native)} / 1 {p.credit_currency}</small></td>
      <td><b>{num(p.consumed_native)} / {num(p.credit_native)}</b><br/><small>остаток {num(p.remaining_native)} {p.credit_currency}</small></td>
      <td><b>{money(p.realized_revenue_rub)}</b><br/><span className={Number(p.realized_profit_rub)>=0?styles.good:styles.bad}>{money(p.realized_profit_rub)}</span><br/><small>{p.operations_count} операций · {num(p.realized_margin_percent,2)}%</small></td>
      <td><div className={styles.actions}>{p.editable&&<button className={styles.button} disabled={busy} onClick={()=>startEdit(p)}>Изменить</button>}{p.cancellable&&<button className={styles.button} disabled={busy} onClick={()=>void orderAction(p,"cancel_purchase")}>Отменить</button>}{p.deletable&&<button className={`${styles.button} ${styles.danger}`} disabled={busy} onClick={()=>void orderAction(p,"delete_purchase")}>Удалить</button>}</div>{!p.editable&&p.operations_count>0&&<small>есть списания — финансовые поля заблокированы</small>}</td>
    </tr>)}</tbody></table>}
   </section>
  </>}
 </>;
}
