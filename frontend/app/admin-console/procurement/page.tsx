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
type OverheadPolicy={id:string;tax_percent:string;topup_fee_percent:string;other_expenses_percent:string;refund_withdrawal_percent:string;total_percent:string;effective_from:string;reason:string};
type OverheadData={policy:OverheadPolicy;models:Array<{model:string;input_margin_percent?:string;output_margin_percent?:string;margin_allowed?:boolean;error?:string}>;models_below_floor?:unknown[]};

const money=(v:string|number|null|undefined)=>`${Number(v??0).toFixed(2).replace(".",",")} ₽`;
const num=(v:string|number|null|undefined,d=2)=>Number(v??0).toFixed(d).replace(".",",");
const healthLabel=(p:Price)=>p.connection.keys_healthy>0&&p.connection.provider_health==="healthy"?"API работает":p.connection.keys_total===0?"Нет API-ключа":"API требует проверки";

export default function EconomicsPage(){
 const[data,setData]=useState<Data|null>(null);const[overheads,setOverheads]=useState<OverheadData|null>(null);
 const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[notice,setNotice]=useState("");
 const[globalMarkup,setGlobalMarkup]=useState("50");const[markup,setMarkup]=useState<Record<string,string>>({});
 const[overDraft,setOverDraft]=useState({tax_percent:"0",topup_fee_percent:"0",other_expenses_percent:"0",refund_withdrawal_percent:"0"});
 const load=async()=>{setBusy(true);try{const [result,oh]=await Promise.all([api<Data>("/admin/procurement/?stress_usd_rub=200&target_margin_percent=35"),api<OverheadData>("/admin/procurement/overheads/")]);setData(result);setOverheads(oh);setMarkup(Object.fromEntries(result.prices.map(p=>[p.model,p.effective_markup_percent??"50"])));setOverDraft({tax_percent:oh.policy.tax_percent,topup_fee_percent:oh.policy.topup_fee_percent,other_expenses_percent:oh.policy.other_expenses_percent,refund_withdrawal_percent:oh.policy.refund_withdrawal_percent});setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить экономику")}finally{setBusy(false)}};
 useEffect(()=>{void load()},[]);
 const post=async<T=unknown>(payload:Record<string,unknown>)=>api<T>("/admin/procurement/",{method:"POST",body:JSON.stringify(payload)});
 const sync=async()=>{setBusy(true);setError("");setNotice("");try{const r=await post<SyncResult>({action:"sync_official_prices"});setNotice(`Официальные цены обновлены: ${r.verified.length}; не распознано: ${r.unsupported.length}; отклонено проверкой: ${r.rejected.length}. USD/RUB: ${r.usd_rub??"—"}.`);await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось проверить официальные цены")}finally{setBusy(false)}};
 const applyMarkup=async(p:Price)=>{const value=markup[p.model]??"";if(!value)return;setBusy(true);setError("");try{const r=await post<{markup_percent:string;resulting_margin_percent:string}>({action:"set_markup",model:p.model,markup_percent:value});setNotice(`${p.model_name}: наценка ${r.markup_percent}%. Финальная маржа ниже учитывает также налог и другие расходы.`);await load()}catch(e){setError(e instanceof Error?e.message:"Наценка не сохранена")}finally{setBusy(false)}};
 const applyGlobal=async()=>{setBusy(true);setError("");try{const r=await post<{markup_percent:string;resulting_margin_percent:string}>({action:"set_global_markup",markup_percent:globalMarkup});setNotice(`Глобальная наценка ${r.markup_percent}%. Дополнительные расходы применяются сверху отдельно.`);await load()}catch(e){setError(e instanceof Error?e.message:"Глобальная наценка не сохранена")}finally{setBusy(false)}};
 const applyOverheads=async()=>{setBusy(true);setError("");setNotice("");try{const r=await api<OverheadData>("/admin/procurement/overheads/",{method:"POST",body:JSON.stringify(overDraft)});const blocked=r.models_below_floor?.length??0;setNotice(`Расходы сохранены. Суммарная надбавка расходов: ${r.policy.total_percent}%.${blocked?` Моделей ниже минимальной маржи: ${blocked}. Увеличьте наценку для них.`:""}`);await load()}catch(e){setError(e instanceof Error?e.message:"Дополнительные расходы не сохранены")}finally{setBusy(false)}};
 const configured=useMemo(()=>data?.prices.filter(p=>p.configured)??[],[data]);void configured;
 const overheadPct=Number(overheads?.policy.total_percent??0);
 const econCost=(v:string|null|undefined)=>Number(v??0)*(1+overheadPct/100);
 const expense=(v:string|null|undefined,pct:string)=>Number(v??0)*Number(pct||0)/100;
 return <>
  <header className={styles.header}><div><h1>Экономика AI</h1><p>Закупка у провайдера → курс валюты → расходы → наценка → публичная цена клиенту → прибыль.</p></div><div className={styles.actions}><button className={`${styles.button} ${styles.primary}`} disabled={busy} onClick={()=>void sync()}>{busy?"Проверяем…":"Обновить официальные цены"}</button></div></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  {data&&overheads&&<>
   <div className={styles.notice}><b>Как считается цена:</b> цена клиенту = закупка в ₽ × [1 + (наценка + налог + комиссия пополнения + прочие расходы + резерв возврата) / 100]. Прибыль = цена клиенту − закупка − все указанные расходы. Input и output считаются отдельно за 1 млн токенов.</div>
   <section className={styles.grid}>
    <div className={styles.card}><small>Выручка</small><strong>{money(data.summary.revenue_rub)}</strong></div>
    <div className={styles.card}><small>API-закупка</small><strong>{money(data.summary.nominal_provider_cost_rub)}</strong></div>
    <div className={styles.card}><small>Валовая прибыль</small><strong>{money(data.summary.gross_profit_nominal_rub)}</strong><small>{num(data.summary.gross_margin_nominal_percent)}%</small></div>
    <div className={styles.card}><small>Экономическая прибыль</small><strong>{money(data.summary.gross_profit_economic_rub)}</strong><small>{num(data.summary.gross_margin_economic_percent)}%</small></div>
    <div className={styles.card}><small>Доп. расходы к закупке</small><strong>{num(overheads.policy.total_percent)}%</strong></div>
    <div className={styles.card}><small>USD/RUB по моделям</small><strong>{data.prices.find(p=>p.fx_rate_rub)?.fx_rate_rub?num(data.prices.find(p=>p.fx_rate_rub)?.fx_rate_rub):"—"} ₽</strong></div>
   </section>
   <section className={styles.section}>
    <h2>Дополнительные расходы, %</h2><p>Эти проценты добавляются к цене сверх закупки и не считаются прибылью сервиса. Новые значения применяются только к новым запросам.</p>
    <div className={styles.filters}>
     <label>Налог, %<input style={{width:110}} inputMode="decimal" value={overDraft.tax_percent} onChange={e=>setOverDraft(v=>({...v,tax_percent:e.target.value}))}/></label>
     <label>Комиссия пополнения, %<input style={{width:110}} inputMode="decimal" value={overDraft.topup_fee_percent} onChange={e=>setOverDraft(v=>({...v,topup_fee_percent:e.target.value}))}/></label>
     <label>Прочие расходы, %<input style={{width:110}} inputMode="decimal" value={overDraft.other_expenses_percent} onChange={e=>setOverDraft(v=>({...v,other_expenses_percent:e.target.value}))}/></label>
     <label>Возврат / вывод, %<input style={{width:110}} inputMode="decimal" value={overDraft.refund_withdrawal_percent} onChange={e=>setOverDraft(v=>({...v,refund_withdrawal_percent:e.target.value}))}/></label>
     <button className={`${styles.button} ${styles.primary}`} disabled={busy} onClick={()=>void applyOverheads()}>Сохранить расходы</button>
    </div>
    <p><b>Итого дополнительных расходов: {num(overheads.policy.total_percent)}%</b></p>
   </section>
   <section className={styles.section}>
    <h2>Базовая наценка сервиса</h2><p>Это именно заработок сервиса поверх закупки. Налог и комиссии задаются отдельно выше.</p>
    <div className={styles.actions}><label>Наценка, % <input style={{width:100}} inputMode="decimal" value={globalMarkup} onChange={e=>setGlobalMarkup(e.target.value)}/></label><button className={styles.button} disabled={busy} onClick={()=>void applyGlobal()}>Установить глобально</button></div>
   </section>
   <section className={styles.section}><h2>Закупка и продажа моделей</h2><p>Все суммы ниже — за 1 млн токенов. Верхняя строка в ячейке — input, нижняя — output.</p><table className={styles.table}><thead><tr><th>Модель</th><th>Закупка у провайдера</th><th>Закупка ₽</th><th>Расходы поверх закупки</th><th>Полная себестоимость</th><th>Наценка</th><th>Цена клиенту</th><th>Прибыль</th><th>Маржа</th></tr></thead><tbody>{data.prices.map(p=><tr key={p.model}>
    <td><b>{p.model_name}</b><br/><small>{p.provider_name} · {p.upstream_model||"ID не выбран"}</small><br/><span className={p.connection.keys_healthy>0?styles.good:styles.bad}>{healthLabel(p)}</span></td>
    {!p.configured?<td colSpan={8}><b className={styles.bad}>Нет закупочной цены.</b><br/><small>{p.official?.supported?"Нажмите «Обновить официальные цены».":"Модель не распознаётся официальным каталогом."}</small></td>:<>
     <td><b>IN ${num(p.provider_input_per_million_native,4)}</b><br/><b>OUT ${num(p.provider_output_per_million_native,4)}</b><br/><small>за 1M · курс {num(p.fx_rate_rub,4)} ₽/$</small>{p.official?.source_url&&<><br/><a href={p.official.source_url} target="_blank" rel="noreferrer">официальный прайс ↗</a></>}</td>
     <td><b>IN {money(p.provider_input_per_million_rub)}</b><br/><b>OUT {money(p.provider_output_per_million_rub)}</b><br/><small>чистая API-закупка</small></td>
     <td><small>налог {overheads.policy.tax_percent}%: {money(expense(p.provider_input_per_million_rub,overheads.policy.tax_percent))} / {money(expense(p.provider_output_per_million_rub,overheads.policy.tax_percent))}</small><br/><small>пополнение {overheads.policy.topup_fee_percent}%: {money(expense(p.provider_input_per_million_rub,overheads.policy.topup_fee_percent))} / {money(expense(p.provider_output_per_million_rub,overheads.policy.topup_fee_percent))}</small><br/><small>прочие {overheads.policy.other_expenses_percent}%: {money(expense(p.provider_input_per_million_rub,overheads.policy.other_expenses_percent))} / {money(expense(p.provider_output_per_million_rub,overheads.policy.other_expenses_percent))}</small><br/><small>возврат {overheads.policy.refund_withdrawal_percent}%: {money(expense(p.provider_input_per_million_rub,overheads.policy.refund_withdrawal_percent))} / {money(expense(p.provider_output_per_million_rub,overheads.policy.refund_withdrawal_percent))}</small></td>
     <td><b>IN {money(econCost(p.provider_input_per_million_rub))}</b><br/><b>OUT {money(econCost(p.provider_output_per_million_rub))}</b><br/><small>закупка + {num(overheads.policy.total_percent)}%</small></td>
     <td><input style={{width:80}} inputMode="decimal" value={markup[p.model]??""} onChange={e=>setMarkup(v=>({...v,[p.model]:e.target.value}))}/>%<br/><button className={styles.button} disabled={busy} onClick={()=>void applyMarkup(p)}>Сохранить</button></td>
     <td><b>IN {money(p.retail_input_per_million_rub)}</b><br/><b>OUT {money(p.retail_output_per_million_rub)}</b><br/><small>эту цену можно публично показывать клиенту</small></td>
     <td><b>IN {money(p.input_profit_per_million_rub)}</b><br/><b>OUT {money(p.output_profit_per_million_rub)}</b><br/><small>после всех указанных расходов</small></td>
     <td className={p.below_margin_floor?styles.bad:styles.good}><b>IN {num(p.input_margin_percent)}%</b><br/><b>OUT {num(p.output_margin_percent)}%</b><br/><small>floor {p.minimum_margin_percent}%</small></td>
    </>}
   </tr>)}</tbody></table></section>
   <section className={styles.section}><h2>Остатки API</h2><p>Баланс ключей проверяется на странице «AI-провайдеры». Здесь — закупочные аккаунты и фактический расход.</p>{data.accounts.length===0?<p>Закупочные аккаунты пока не заведены.</p>:<table className={styles.table}><thead><tr><th>Провайдер</th><th>Аккаунт</th><th>Доступно</th><th>Потрачено</th></tr></thead><tbody>{data.accounts.map(a=><tr key={a.id}><td>{a.provider_name}</td><td>{a.label}</td><td>{num(a.available_native,4)} {a.currency}</td><td>{num(a.spent_native,4)} {a.currency}</td></tr>)}</tbody></table>}</section>
   <div className={styles.notice}><b>Связка с клиентским списанием:</b> backend использует тот же pricing engine, что и этот экран. В pricing snapshot каждого запроса сохраняются курс валюты, закупочная цена, наценка, налог, комиссия пополнения, прочие расходы и резерв возврата. Поэтому историческое списание не меняется задним числом после изменения настроек.</div>
  </>}
 </>;
}
