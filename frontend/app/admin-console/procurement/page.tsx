"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type Official={supported:boolean;source_url:string};
type Connection={provider_enabled:boolean;provider_health:string;model_enabled:boolean;keys_total:number;keys_healthy:number};
type Price={model:string;model_name:string;upstream_model:string;provider_name:string;configured:boolean;currency?:string;provider_input_per_million_native?:string|null;provider_output_per_million_native?:string|null;fx_rate_rub?:string|null;provider_input_per_million_rub?:string|null;provider_output_per_million_rub?:string|null;retail_input_per_million_rub?:string|null;retail_output_per_million_rub?:string|null;effective_markup_percent?:string|null;minimum_margin_percent?:string;official:Official;connection:Connection};
type Account={id:string;provider_name:string;label:string;currency:string;available_native:string;spent_native:string;low_balance:boolean};
type Data={summary:{revenue_rub:string;nominal_provider_cost_rub:string;gross_profit_nominal_rub:string;gross_margin_nominal_percent:string;gross_profit_economic_rub:string;gross_margin_economic_percent:string};prices:Price[];accounts:Account[]};
type SyncResult={verified:unknown[];rejected:unknown[];unsupported:unknown[];usd_rub:string|null};
type OverheadPolicy={tax_percent:string;topup_fee_percent:string;other_expenses_percent:string;refund_withdrawal_percent:string;total_percent:string};
type OverheadData={policy:OverheadPolicy;models_below_floor?:unknown[]};

const money=(v:string|number|null|undefined)=>`${Number(v??0).toFixed(2).replace(".",",")} ₽`;
const num=(v:string|number|null|undefined,d=2)=>Number(v??0).toFixed(d).replace(".",",");

export default function ProcurementEconomicsPage(){
 const[data,setData]=useState<Data|null>(null);const[overheads,setOverheads]=useState<OverheadData|null>(null);
 const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[notice,setNotice]=useState("");
 const[globalMarkup,setGlobalMarkup]=useState("50");const[markup,setMarkup]=useState<Record<string,string>>({});
 const[overDraft,setOverDraft]=useState({tax_percent:"0",topup_fee_percent:"0",other_expenses_percent:"0",refund_withdrawal_percent:"0"});
 const load=async()=>{setBusy(true);try{const[result,oh]=await Promise.all([api<Data>("/admin/procurement/?stress_usd_rub=200&target_margin_percent=35"),api<OverheadData>("/admin/procurement/overheads/")]);setData(result);setOverheads(oh);setMarkup(Object.fromEntries(result.prices.map(p=>[p.model,p.effective_markup_percent??"50"])));setOverDraft({tax_percent:oh.policy.tax_percent,topup_fee_percent:oh.policy.topup_fee_percent,other_expenses_percent:oh.policy.other_expenses_percent,refund_withdrawal_percent:oh.policy.refund_withdrawal_percent});setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить экономику")}finally{setBusy(false)}};
 useEffect(()=>{void load()},[]);
 const post=async<T=unknown>(payload:Record<string,unknown>)=>api<T>("/admin/procurement/",{method:"POST",body:JSON.stringify(payload)});
 const sync=async()=>{setBusy(true);setError("");try{const r=await post<SyncResult>({action:"sync_official_prices"});setNotice(`Официальные цены обновлены: ${r.verified.length}; не распознано: ${r.unsupported.length}; отклонено: ${r.rejected.length}; USD/RUB: ${r.usd_rub??"—"}.`);await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось обновить цены")}finally{setBusy(false)}};
 const applyOverheads=async()=>{setBusy(true);setError("");try{const r=await api<OverheadData>("/admin/procurement/overheads/",{method:"POST",body:JSON.stringify(overDraft)});setNotice(`Расходы сохранены. Общая надбавка расходов: ${r.policy.total_percent}%.`);await load()}catch(e){setError(e instanceof Error?e.message:"Расходы не сохранены")}finally{setBusy(false)}};
 const applyGlobal=async()=>{setBusy(true);setError("");try{await post({action:"set_global_markup",markup_percent:globalMarkup});setNotice(`Глобальная наценка ${globalMarkup}% сохранена.`);await load()}catch(e){setError(e instanceof Error?e.message:"Наценка не сохранена")}finally{setBusy(false)}};
 const applyMarkup=async(p:Price)=>{setBusy(true);setError("");try{await post({action:"set_markup",model:p.model,markup_percent:markup[p.model]??""});setNotice(`${p.model_name}: наценка сохранена.`);await load()}catch(e){setError(e instanceof Error?e.message:"Наценка модели не сохранена")}finally{setBusy(false)}};
 const overheadPct=Number(overheads?.policy.total_percent??0);
 const econCost=(v:string|null|undefined)=>Number(v??0)*(1+overheadPct/100);
 const econProfit=(sale:string|null|undefined,cost:string|null|undefined)=>Number(sale??0)-econCost(cost);
 const econMargin=(sale:string|null|undefined,cost:string|null|undefined)=>{const s=Number(sale??0);return s>0?econProfit(sale,cost)/s*100:0};

 return <>
  <header className={styles.header}><div><h1>Экономика AI</h1><p>Цены моделей, расходы, наценка и маржа. API-ключи и закупочные документы вынесены в отдельные вкладки.</p></div><button className={`${styles.button} ${styles.primary}`} disabled={busy} onClick={()=>void sync()}>{busy?"Обновляем…":"Обновить официальные цены"}</button></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  {data&&overheads&&<>
   <section className={styles.grid}>
    <div className={styles.card}><small>Выручка</small><strong>{money(data.summary.revenue_rub)}</strong></div>
    <div className={styles.card}><small>API-закупка по запросам</small><strong>{money(data.summary.nominal_provider_cost_rub)}</strong></div>
    <div className={styles.card}><small>Валовая прибыль</small><strong>{money(data.summary.gross_profit_nominal_rub)}</strong><small>{num(data.summary.gross_margin_nominal_percent)}%</small></div>
    <div className={styles.card}><small>Экономическая прибыль</small><strong>{money(data.summary.gross_profit_economic_rub)}</strong><small>{num(data.summary.gross_margin_economic_percent)}%</small></div>
    <div className={styles.card}><small>Дополнительные расходы</small><strong>{num(overheads.policy.total_percent)}%</strong></div>
   </section>

   <section className={styles.section}><h2>Дополнительные расходы</h2><p>Налог, комиссия пополнения, операционные расходы и резерв возвратов учитываются отдельно от прибыли.</p><div className={styles.filters}>
    <label>Налог, %<input inputMode="decimal" value={overDraft.tax_percent} onChange={e=>setOverDraft(v=>({...v,tax_percent:e.target.value}))}/></label>
    <label>Комиссия пополнения, %<input inputMode="decimal" value={overDraft.topup_fee_percent} onChange={e=>setOverDraft(v=>({...v,topup_fee_percent:e.target.value}))}/></label>
    <label>Прочие расходы, %<input inputMode="decimal" value={overDraft.other_expenses_percent} onChange={e=>setOverDraft(v=>({...v,other_expenses_percent:e.target.value}))}/></label>
    <label>Возврат / вывод, %<input inputMode="decimal" value={overDraft.refund_withdrawal_percent} onChange={e=>setOverDraft(v=>({...v,refund_withdrawal_percent:e.target.value}))}/></label>
    <button className={`${styles.button} ${styles.primary}`} disabled={busy} onClick={()=>void applyOverheads()}>Сохранить расходы</button>
   </div></section>

   <section className={styles.section}><h2>Базовая наценка сервиса</h2><div className={styles.actions}><label>Наценка, % <input style={{width:100}} inputMode="decimal" value={globalMarkup} onChange={e=>setGlobalMarkup(e.target.value)}/></label><button className={styles.button} disabled={busy} onClick={()=>void applyGlobal()}>Установить глобально</button></div></section>

   <section className={styles.section}><h2>Закупка и продажа моделей</h2><p>Цена модели — отдельно от закупочных ордеров ключей. Здесь задаётся коммерческая экономика 1 млн токенов.</p><table className={styles.table}><thead><tr><th>Модель</th><th>Закупочная цена</th><th>Курс / источник</th><th>Наценка</th><th>Цена клиенту</th><th>Экономическая прибыль</th><th>Маржа</th></tr></thead><tbody>{data.prices.map(p=><tr key={p.model}>
    <td><b>{p.model_name}</b><br/><small>{p.provider_name} · {p.upstream_model||"ID не выбран"}</small></td>
    {!p.configured?<td colSpan={6}><span className={styles.bad}>Закупочная цена не настроена.</span></td>:<>
     <td><b>IN {money(p.provider_input_per_million_rub)}</b><br/><b>OUT {money(p.provider_output_per_million_rub)}</b></td>
     <td>{p.currency||"—"} · {p.fx_rate_rub?`${num(p.fx_rate_rub,4)} ₽`:"—"}{p.official?.source_url&&<><br/><a href={p.official.source_url} target="_blank" rel="noreferrer">официальный прайс ↗</a></>}</td>
     <td><input style={{width:80}} inputMode="decimal" value={markup[p.model]??""} onChange={e=>setMarkup(v=>({...v,[p.model]:e.target.value}))}/>%<br/><button className={styles.button} disabled={busy} onClick={()=>void applyMarkup(p)}>Сохранить</button></td>
     <td><b>IN {money(p.retail_input_per_million_rub)}</b><br/><b>OUT {money(p.retail_output_per_million_rub)}</b></td>
     <td><b>IN {money(econProfit(p.retail_input_per_million_rub,p.provider_input_per_million_rub))}</b><br/><b>OUT {money(econProfit(p.retail_output_per_million_rub,p.provider_output_per_million_rub))}</b></td>
     <td className={(econMargin(p.retail_input_per_million_rub,p.provider_input_per_million_rub)<Number(p.minimum_margin_percent??0)||econMargin(p.retail_output_per_million_rub,p.provider_output_per_million_rub)<Number(p.minimum_margin_percent??0))?styles.bad:styles.good}><b>IN {num(econMargin(p.retail_input_per_million_rub,p.provider_input_per_million_rub))}%</b><br/><b>OUT {num(econMargin(p.retail_output_per_million_rub,p.provider_output_per_million_rub))}%</b></td>
    </>}
   </tr>)}</tbody></table></section>

   <section className={styles.section}><h2>Сводный внутренний остаток</h2>{data.accounts.length===0?<p>Закупочных счетов пока нет.</p>:<table className={styles.table}><thead><tr><th>Провайдер</th><th>Счёт</th><th>Доступно</th><th>Потрачено</th></tr></thead><tbody>{data.accounts.map(a=><tr key={a.id}><td>{a.provider_name}</td><td>{a.label}</td><td>{num(a.available_native,4)} {a.currency}</td><td>{num(a.spent_native,4)} {a.currency}</td></tr>)}</tbody></table>}</section>
  </>}
 </>;
}
