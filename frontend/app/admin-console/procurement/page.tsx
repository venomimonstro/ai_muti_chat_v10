"use client";

import {useEffect,useMemo,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type Official={supported:boolean;source_url:string;basis:string;note:string;effective_until:string|null};
type Connection={provider_enabled:boolean;provider_health:string;model_enabled:boolean;keys_total:number;keys_healthy:number};
type Price={
 model:string;model_name:string;upstream_model:string;provider:string;provider_name:string;configured:boolean;currency?:string;
 provider_input_per_million_native?:string|null;provider_output_per_million_native?:string|null;fx_rate_rub?:string|null;
 provider_input_per_million_rub?:string|null;provider_output_per_million_rub?:string|null;
 retail_input_per_million_rub?:string|null;retail_output_per_million_rub?:string|null;
 input_profit_per_million_rub?:string|null;output_profit_per_million_rub?:string|null;
 input_markup_percent?:string|null;output_markup_percent?:string|null;effective_markup_percent?:string|null;
 input_margin_percent?:string|null;output_margin_percent?:string|null;minimum_margin_percent?:string;
 below_margin_floor?:boolean;stress_unprofitable?:boolean;price_checked_at?:string;official:Official;connection:Connection;
};
type Account={id:string;provider_name:string;label:string;currency:string;available_native:string;spent_native:string;low_balance:boolean};
type Data={
 pricing_policy:{minimum_margin_percent:string;target_margin_percent:string;formula_markup:string;formula_margin:string};
 summary:{revenue_rub:string;nominal_provider_cost_rub:string;gross_profit_nominal_rub:string;gross_margin_nominal_percent:string;gross_profit_economic_rub:string;gross_margin_economic_percent:string;allocation_coverage_percent:string};
 risk:{low_balance_accounts:number;unallocated_completed_operations:number;stress_unprofitable_models:number;below_margin_floor_models:number;models_without_cost:number};
 prices:Price[];accounts:Account[];
};
type SyncResult={verified:Array<{model:string;upstream_model:string;input_usd_per_million:string;output_usd_per_million:string;basis:string;source_url:string}>;rejected:unknown[];unsupported:unknown[];expired:unknown[];usd_rub:string|null};

const money=(v:string|number|null|undefined)=>`${Number(v??0).toFixed(2).replace(".",",")} ₽`;
const num=(v:string|number|null|undefined,d=2)=>Number(v??0).toFixed(d).replace(".",",");
const healthLabel=(p:Price)=>p.connection.keys_healthy>0&&p.connection.provider_health==="healthy"?"API работает":p.connection.keys_total===0?"Нет API-ключа":"API требует проверки";

export default function EconomicsPage(){
 const[data,setData]=useState<Data|null>(null);
 const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[notice,setNotice]=useState("");
 const[globalMarkup,setGlobalMarkup]=useState("50");const[markup,setMarkup]=useState<Record<string,string>>({});
 const load=async()=>{setBusy(true);try{const result=await api<Data>("/admin/procurement/?stress_usd_rub=200&target_margin_percent=35");setData(result);setMarkup(Object.fromEntries(result.prices.map(p=>[p.model,p.effective_markup_percent??"50"])));setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить экономику")}finally{setBusy(false)}};
 useEffect(()=>{void load()},[]);
 const post=async<T=unknown>(payload:Record<string,unknown>)=>api<T>("/admin/procurement/",{method:"POST",body:JSON.stringify(payload)});
 const sync=async()=>{setBusy(true);setError("");setNotice("");try{const r=await post<SyncResult>({action:"sync_official_prices"});setNotice(`Официальные цены обновлены: ${r.verified.length}; не распознано: ${r.unsupported.length}; отклонено проверкой: ${r.rejected.length}. USD/RUB: ${r.usd_rub??"—"}.`);await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось проверить официальные цены")}finally{setBusy(false)}};
 const applyMarkup=async(p:Price)=>{const value=markup[p.model]??"";if(!value)return;setBusy(true);setError("");try{const r=await post<{markup_percent:string;resulting_margin_percent:string}>({action:"set_markup",model:p.model,markup_percent:value});setNotice(`${p.model_name}: наценка ${r.markup_percent}%, расчётная маржа ${r.resulting_margin_percent}%.`);await load()}catch(e){setError(e instanceof Error?e.message:"Наценка не сохранена")}finally{setBusy(false)}};
 const applyGlobal=async()=>{setBusy(true);setError("");try{const r=await post<{markup_percent:string;resulting_margin_percent:string}>({action:"set_global_markup",markup_percent:globalMarkup});setNotice(`Глобальная наценка ${r.markup_percent}%, маржа ${r.resulting_margin_percent}%. Индивидуальные правила моделей имеют приоритет.`);await load()}catch(e){setError(e instanceof Error?e.message:"Глобальная наценка не сохранена")}finally{setBusy(false)}};
 const configured=useMemo(()=>data?.prices.filter(p=>p.configured)??[],[data]);
 return <>
  <header className={styles.header}><div><h1>Экономика AI</h1><p>Официальная закупочная стоимость → наценка → цена клиенту → прибыль сервиса.</p></div><div className={styles.actions}><button className={`${styles.button} ${styles.primary}`} disabled={busy} onClick={()=>void sync()}>{busy?"Проверяем…":"Обновить официальные цены"}</button></div></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  {data&&<>
   <div className={styles.notice}><b>Наценка и маржа — разные вещи.</b> При наценке 50% товар себестоимостью 100 ₽ продаётся за 150 ₽, прибыль 50 ₽, а маржа 33,33%. Система не разрешает наценку, которая опускает маржу ниже {data.pricing_policy.minimum_margin_percent}%.</div>
   <section className={styles.grid}>
    <div className={styles.card}><small>Выручка</small><strong>{money(data.summary.revenue_rub)}</strong></div>
    <div className={styles.card}><small>Себестоимость API</small><strong>{money(data.summary.nominal_provider_cost_rub)}</strong></div>
    <div className={styles.card}><small>Валовая прибыль</small><strong>{money(data.summary.gross_profit_nominal_rub)}</strong><small>{num(data.summary.gross_margin_nominal_percent)}%</small></div>
    <div className={styles.card}><small>Экономическая прибыль</small><strong>{money(data.summary.gross_profit_economic_rub)}</strong><small>{num(data.summary.gross_margin_economic_percent)}%</small></div>
    <div className={styles.card}><small>Без закупочной цены</small><strong>{data.risk.models_without_cost}</strong></div>
    <div className={styles.card}><small>Ниже минимальной маржи</small><strong>{data.risk.below_margin_floor_models}</strong></div>
   </section>
   <section className={styles.section}>
    <h2>Базовая наценка</h2><p>Применяется ко всем моделям в режиме наценки, если для конкретной модели не задано собственное правило.</p>
    <div className={styles.actions}><label>Наценка, % <input style={{width:100}} inputMode="decimal" value={globalMarkup} onChange={e=>setGlobalMarkup(e.target.value)}/></label><button className={styles.button} disabled={busy} onClick={()=>void applyGlobal()}>Установить глобально</button></div>
   </section>
   <section className={styles.section}><h2>Модели и заработок</h2><table className={styles.table}><thead><tr><th>Модель / API</th><th>Официальная закупка</th><th>Наценка</th><th>Продажа клиенту</th><th>Прибыль</th><th>Маржа</th><th>Риск</th></tr></thead><tbody>{data.prices.map(p=><tr key={p.model}>
    <td><b>{p.model_name}</b><br/><small>{p.provider_name} · {p.upstream_model||"ID не выбран"}</small><br/><span className={p.connection.keys_healthy>0?styles.good:styles.bad}>{healthLabel(p)}</span><br/><small>ключей: {p.connection.keys_healthy}/{p.connection.keys_total}</small></td>
    {!p.configured?<td colSpan={6}><b className={styles.bad}>Нет закупочной цены.</b><br/><small>{p.official?.supported?"Нажмите «Обновить официальные цены».":"Модель пока не распознаётся официальным каталогом — не включайте её коммерчески."}</small>{p.official?.source_url&&<><br/><a href={p.official.source_url} target="_blank" rel="noreferrer">Официальный источник ↗</a></>}</td>:<>
     <td><b>${num(p.provider_input_per_million_native)} / ${num(p.provider_output_per_million_native)}</b><br/><small>input/output за 1M</small><br/>{money(p.provider_input_per_million_rub)} / {money(p.provider_output_per_million_rub)}<br/><small>курс {num(p.fx_rate_rub)} ₽/$</small>{p.official?.source_url&&<><br/><a href={p.official.source_url} target="_blank" rel="noreferrer">официальный прайс ↗</a></>}<br/><small>{p.official?.note||p.official?.basis}</small></td>
     <td><input style={{width:80}} inputMode="decimal" value={markup[p.model]??""} onChange={e=>setMarkup(v=>({...v,[p.model]:e.target.value}))}/>%<br/><button className={styles.button} disabled={busy} onClick={()=>void applyMarkup(p)}>Сохранить</button><br/><small>факт input/output: {num(p.input_markup_percent)}% / {num(p.output_markup_percent)}%</small></td>
     <td><b>{money(p.retail_input_per_million_rub)}</b><br/><b>{money(p.retail_output_per_million_rub)}</b><br/><small>input / output за 1M</small></td>
     <td><b>{money(p.input_profit_per_million_rub)}</b><br/><b>{money(p.output_profit_per_million_rub)}</b><br/><small>input / output за 1M</small></td>
     <td className={p.below_margin_floor?styles.bad:styles.good}><b>{num(p.input_margin_percent)}%</b><br/><b>{num(p.output_margin_percent)}%</b><br/><small>минимум {p.minimum_margin_percent}%</small></td>
     <td>{p.below_margin_floor?<b className={styles.bad}>НИЖЕ FLOOR</b>:p.stress_unprofitable?<b className={styles.bad}>РИСК ПРИ КУРСЕ</b>:<b className={styles.good}>OK</b>}<br/><small>цена проверена {p.price_checked_at?new Date(p.price_checked_at).toLocaleString("ru-RU"):"—"}</small></td>
    </>}
   </tr>)}</tbody></table></section>
   <section className={styles.section}><h2>Остатки API</h2><p>Баланс ключа показывается на странице «AI-провайдеры». Здесь остаётся финансовый учёт закупочных аккаунтов и фактического расхода.</p>{data.accounts.length===0?<p>Закупочные аккаунты пока не заведены.</p>:<table className={styles.table}><thead><tr><th>Провайдер</th><th>Аккаунт</th><th>Доступно</th><th>Потрачено</th></tr></thead><tbody>{data.accounts.map(a=><tr key={a.id}><td>{a.provider_name}</td><td>{a.label}</td><td>{num(a.available_native,4)} {a.currency}</td><td>{num(a.spent_native,4)} {a.currency}</td></tr>)}</tbody></table>}</section>
   <div className={styles.notice}><b>Связка с клиентом:</b> новая цена применяется только к новым запросам. Каждый AI-запрос получает immutable pricing snapshot, резервирует максимум, после ответа списывает фактическую стоимость, а разница остаётся валовой прибылью сервиса. Модели ниже margin floor блокируются до вызова провайдера.</div>
  </>}
 </>;
}
