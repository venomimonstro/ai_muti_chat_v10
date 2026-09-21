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
type LedgerKey={id:string;provider:string;provider_name:string;label:string;masked:string;enabled:boolean;health_state:string;provider_balance_amount:string|null;provider_balance_currency:string;provider_balance_supported:boolean;account_id:string|null;account_currency:string|null;ledger_available_native:string|null;ledger_spent_native:string|null;is_default:boolean};
type Purchase={id:string;document_number:string;provider:string;provider_name:string;account_id:string;account_label:string;api_key_id:string|null;api_key_label:string;api_key_masked:string;credit_native:string;credit_currency:string;consumed_native:string;remaining_native:string;payment_amount:string|null;payment_currency:string;payment_fx_rate_rub:string|null;market_fx_rate_rub:string|null;base_cost_rub:string;fees_rub:string;total_cash_outlay_rub:string;effective_cost_rub_per_native:string;realized_revenue_rub:string;realized_cost_rub:string;realized_profit_rub:string;realized_margin_percent:string;operations_count:number;reference:string;purchased_at:string};
type LedgerData={summary:{purchase_documents:number;cash_outlay_rub:string;fees_rub:string;credit_native_total:string;consumed_native_total:string;realized_revenue_rub:string;realized_cost_rub:string;realized_profit_rub:string;realized_margin_percent:string};keys:LedgerKey[];accounts:unknown[];purchases:Purchase[]};

const money=(v:string|number|null|undefined)=>`${Number(v??0).toFixed(2).replace(".",",")} ₽`;
const num=(v:string|number|null|undefined,d=2)=>Number(v??0).toFixed(d).replace(".",",");
const healthLabel=(p:Price)=>p.connection.keys_healthy>0&&p.connection.provider_health==="healthy"?"API работает":p.connection.keys_total===0?"Нет API-ключа":"API требует проверки";

export default function EconomicsPage(){
 const[data,setData]=useState<Data|null>(null);const[overheads,setOverheads]=useState<OverheadData|null>(null);const[ledger,setLedger]=useState<LedgerData|null>(null);
 const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[notice,setNotice]=useState("");
 const[globalMarkup,setGlobalMarkup]=useState("50");const[markup,setMarkup]=useState<Record<string,string>>({});
 const[overDraft,setOverDraft]=useState({tax_percent:"0",topup_fee_percent:"0",other_expenses_percent:"0",refund_withdrawal_percent:"0"});
 const[purchaseDraft,setPurchaseDraft]=useState({api_key_id:"",credit_native:"",credit_currency:"USD",payment_amount:"",payment_currency:"RUB",payment_fx_rate_rub:"",fees_rub:"0",market_fx_rate_rub:"",purchased_at:new Date().toISOString().slice(0,10),reference:""});
 const load=async()=>{setBusy(true);try{const [result,oh,ledgerResult]=await Promise.all([api<Data>("/admin/procurement/?stress_usd_rub=200&target_margin_percent=35"),api<OverheadData>("/admin/procurement/overheads/"),api<LedgerData>("/admin/procurement/ledger/")]);setData(result);setOverheads(oh);setLedger(ledgerResult);setMarkup(Object.fromEntries(result.prices.map(p=>[p.model,p.effective_markup_percent??"50"])));setOverDraft({tax_percent:oh.policy.tax_percent,topup_fee_percent:oh.policy.topup_fee_percent,other_expenses_percent:oh.policy.other_expenses_percent,refund_withdrawal_percent:oh.policy.refund_withdrawal_percent});setPurchaseDraft(v=>({...v,api_key_id:v.api_key_id||ledgerResult.keys[0]?.id||""}));setError("")}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить экономику")}finally{setBusy(false)}};
 useEffect(()=>{void load()},[]);
 const post=async<T=unknown>(payload:Record<string,unknown>)=>api<T>("/admin/procurement/",{method:"POST",body:JSON.stringify(payload)});
 const ledgerPost=async<T=unknown>(payload:Record<string,unknown>)=>api<T>("/admin/procurement/ledger/",{method:"POST",body:JSON.stringify(payload)});
 const sync=async()=>{setBusy(true);setError("");setNotice("");try{const r=await post<SyncResult>({action:"sync_official_prices"});setNotice(`Официальные цены обновлены: ${r.verified.length}; не распознано: ${r.unsupported.length}; отклонено проверкой: ${r.rejected.length}. USD/RUB: ${r.usd_rub??"—"}.`);await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось проверить официальные цены")}finally{setBusy(false)}};
 const applyMarkup=async(p:Price)=>{const value=markup[p.model]??"";if(!value)return;setBusy(true);setError("");try{const r=await post<{markup_percent:string;resulting_margin_percent:string}>({action:"set_markup",model:p.model,markup_percent:value});setNotice(`${p.model_name}: наценка ${r.markup_percent}%. Финальная маржа ниже учитывает также налог и другие расходы.`);await load()}catch(e){setError(e instanceof Error?e.message:"Наценка не сохранена")}finally{setBusy(false)}};
 const applyGlobal=async()=>{setBusy(true);setError("");try{const r=await post<{markup_percent:string;resulting_margin_percent:string}>({action:"set_global_markup",markup_percent:globalMarkup});setNotice(`Глобальная наценка ${r.markup_percent}%. Дополнительные расходы применяются сверху отдельно.`);await load()}catch(e){setError(e instanceof Error?e.message:"Глобальная наценка не сохранена")}finally{setBusy(false)}};
 const applyOverheads=async()=>{setBusy(true);setError("");setNotice("");try{const r=await api<OverheadData>("/admin/procurement/overheads/",{method:"POST",body:JSON.stringify(overDraft)});const blocked=r.models_below_floor?.length??0;setNotice(`Расходы сохранены. Суммарная надбавка расходов: ${r.policy.total_percent}%.${blocked?` Моделей ниже минимальной маржи: ${blocked}. Увеличьте наценку для них.`:""}`);await load()}catch(e){setError(e instanceof Error?e.message:"Дополнительные расходы не сохранены")}finally{setBusy(false)}};
 const recordPurchase=async()=>{if(!purchaseDraft.api_key_id||!purchaseDraft.credit_native||!purchaseDraft.payment_amount){setError("Выберите API-ключ и заполните номинал баланса и фактически оплаченную сумму");return;}setBusy(true);setError("");setNotice("");try{const p=await ledgerPost<Purchase>({action:"purchase_key",...purchaseDraft,payment_fx_rate_rub:purchaseDraft.payment_currency==="RUB"?"":purchaseDraft.payment_fx_rate_rub,base_cost_rub:""});setNotice(`Закупка ${p.document_number} проведена. На ключ зачислено ${p.credit_native} ${p.credit_currency}; фактическая себестоимость ${money(p.total_cash_outlay_rub)}.`);setPurchaseDraft(v=>({...v,credit_native:"",payment_amount:"",fees_rub:"0",reference:""}));await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось провести закупку API")}finally{setBusy(false)}};
 const makeDefault=async(accountId:string)=>{setBusy(true);setError("");try{await ledgerPost({action:"set_default",account_id:accountId});setNotice("Ключ назначен основным закупочным ключом провайдера. Реальные запросы будут использовать именно его.");await load()}catch(e){setError(e instanceof Error?e.message:"Не удалось назначить основной ключ")}finally{setBusy(false)}};
 const configured=useMemo(()=>data?.prices.filter(p=>p.configured)??[],[data]);void configured;
 const overheadPct=Number(overheads?.policy.total_percent??0);
 const econCost=(v:string|null|undefined)=>Number(v??0)*(1+overheadPct/100);
 const expense=(v:string|null|undefined,pct:string)=>Number(v??0)*Number(pct||0)/100;
 const econProfit=(sale:string|null|undefined,cost:string|null|undefined)=>Number(sale??0)-econCost(cost);
 const econMargin=(sale:string|null|undefined,cost:string|null|undefined)=>{const s=Number(sale??0);return s>0?econProfit(sale,cost)/s*100:0};
 return <>
  <header className={styles.header}><div><h1>Экономика AI</h1><p>Закупка конкретного API-ключа → фактический курс и комиссии → списание по партиям → продажи → прибыль.</p></div><div className={styles.actions}><button className={`${styles.button} ${styles.primary}`} disabled={busy} onClick={()=>void sync()}>{busy?"Проверяем…":"Обновить официальные цены"}</button></div></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  {data&&overheads&&ledger&&<>
   <section className={styles.section}>
    <h2>Закупочный учёт API-ключей</h2>
    <p>Это отдельный финансовый регистр. Каждая закупка неизменяема и получает собственный номер документа. Остаток списывается FIFO по реальным запросам клиентов, поэтому прибыль можно анализировать по конкретному ключу и конкретной партии пополнения.</p>
    <section className={styles.grid}>
     <div className={styles.card}><small>Документов закупки</small><strong>{ledger.summary.purchase_documents}</strong></div>
     <div className={styles.card}><small>Фактически вложено</small><strong>{money(ledger.summary.cash_outlay_rub)}</strong></div>
     <div className={styles.card}><small>Комиссии закупок</small><strong>{money(ledger.summary.fees_rub)}</strong></div>
     <div className={styles.card}><small>Выручка по списанным партиям</small><strong>{money(ledger.summary.realized_revenue_rub)}</strong></div>
     <div className={styles.card}><small>Реализованная прибыль</small><strong>{money(ledger.summary.realized_profit_rub)}</strong><small>{num(ledger.summary.realized_margin_percent)}%</small></div>
    </section>
    <h3>Провести закупку API-баланса</h3>
    <div className={styles.notice}><b>Пример:</b> на OpenAI-ключ зачислено $10, а карта/обменник фактически списал $15 по курсу 95 ₽/$ — укажите номинал 10 USD, фактическую оплату 15 USD и курс 95. Реальная себестоимость партии будет 1 425 ₽, то есть 142,50 ₽ за $1 API-баланса.</div>
    <div className={styles.filters}>
     <label>API-ключ<select value={purchaseDraft.api_key_id} onChange={e=>setPurchaseDraft(v=>({...v,api_key_id:e.target.value}))}><option value="">Выберите ключ</option>{ledger.keys.map(k=><option key={k.id} value={k.id}>{k.provider_name} · {k.label} · {k.masked}</option>)}</select></label>
     <label>Номинал на балансе<input inputMode="decimal" placeholder="10" value={purchaseDraft.credit_native} onChange={e=>setPurchaseDraft(v=>({...v,credit_native:e.target.value}))}/></label>
     <label>Валюта баланса<select value={purchaseDraft.credit_currency} onChange={e=>setPurchaseDraft(v=>({...v,credit_currency:e.target.value}))}><option>USD</option><option>EUR</option><option>RUB</option></select></label>
     <label>Фактически оплачено<input inputMode="decimal" placeholder="15" value={purchaseDraft.payment_amount} onChange={e=>setPurchaseDraft(v=>({...v,payment_amount:e.target.value}))}/></label>
     <label>Валюта оплаты<select value={purchaseDraft.payment_currency} onChange={e=>setPurchaseDraft(v=>({...v,payment_currency:e.target.value}))}><option>RUB</option><option>USD</option><option>EUR</option></select></label>
     {purchaseDraft.payment_currency!=="RUB"&&<label>Фактический курс ₽ / 1 {purchaseDraft.payment_currency}<input inputMode="decimal" placeholder="95" value={purchaseDraft.payment_fx_rate_rub} onChange={e=>setPurchaseDraft(v=>({...v,payment_fx_rate_rub:e.target.value}))}/></label>}
     <label>Комиссии отдельно, ₽<input inputMode="decimal" value={purchaseDraft.fees_rub} onChange={e=>setPurchaseDraft(v=>({...v,fees_rub:e.target.value}))}/></label>
     <label>Рыночный курс ЦБ/справочно<input inputMode="decimal" placeholder="необязательно" value={purchaseDraft.market_fx_rate_rub} onChange={e=>setPurchaseDraft(v=>({...v,market_fx_rate_rub:e.target.value}))}/></label>
     <label>Дата<input type="date" value={purchaseDraft.purchased_at} onChange={e=>setPurchaseDraft(v=>({...v,purchased_at:e.target.value}))}/></label>
     <label>Комментарий / чек<input placeholder="например чек обменника" value={purchaseDraft.reference} onChange={e=>setPurchaseDraft(v=>({...v,reference:e.target.value}))}/></label>
     <button className={`${styles.button} ${styles.primary}`} disabled={busy||ledger.keys.length===0} onClick={()=>void recordPurchase()}>Провести закупку</button>
    </div>
    {ledger.keys.length===0&&<p className={styles.bad}>Сначала добавьте API-ключ на странице «AI-провайдеры».</p>}
    {ledger.keys.length>0&&<><h3>Ключи и учётные остатки</h3><table className={styles.table}><thead><tr><th>Провайдер / ключ</th><th>Баланс по нашему учёту</th><th>Фактический баланс провайдера</th><th>Статус</th><th></th></tr></thead><tbody>{ledger.keys.map(k=><tr key={k.id}><td><b>{k.provider_name} · {k.label}</b><br/><small>{k.masked}</small></td><td>{k.account_id?<><b>{num(k.ledger_available_native,6)} {k.account_currency}</b><br/><small>списано {num(k.ledger_spent_native,6)} {k.account_currency}</small></>:"Закупок ещё не было"}</td><td>{k.provider_balance_supported&&k.provider_balance_amount!=null?`${k.provider_balance_amount} ${k.provider_balance_currency}`:"провайдер не отдаёт balance через этот ключ"}</td><td>{k.is_default?<span className={styles.good}>основной закупочный ключ</span>:k.account_id?<span>учёт ведётся</span>:<span className={styles.warn}>нет закупочного счёта</span>}</td><td>{k.account_id&&!k.is_default&&<button className={styles.button} disabled={busy} onClick={()=>void makeDefault(k.account_id!)}>Сделать основным</button>}</td></tr>)}</tbody></table></>}
    <h3>История закупок</h3>
    {ledger.purchases.length===0?<p>Закупок пока нет.</p>:<table className={styles.table}><thead><tr><th>Документ / дата</th><th>Ключ</th><th>Куплено</th><th>Оплата и курс</th><th>Фактическая себестоимость</th><th>Списано / остаток</th><th>Продажи</th><th>Прибыль</th></tr></thead><tbody>{ledger.purchases.map(p=><tr key={p.id}><td><b>{p.document_number}</b><br/><small>{new Date(p.purchased_at).toLocaleDateString("ru-RU")}</small>{p.reference&&<><br/><small>{p.reference}</small></>}</td><td><b>{p.provider_name}</b><br/>{p.api_key_label}<br/><small>{p.api_key_masked}</small></td><td><b>{num(p.credit_native,6)} {p.credit_currency}</b></td><td>{p.payment_amount!=null?<><b>{num(p.payment_amount,6)} {p.payment_currency}</b>{p.payment_fx_rate_rub&&<><br/><small>факт. курс {num(p.payment_fx_rate_rub,4)} ₽</small></>}{p.market_fx_rate_rub&&<><br/><small>рынок {num(p.market_fx_rate_rub,4)} ₽</small></>}</>:<span>историческая запись</span>}{Number(p.fees_rub)>0&&<><br/><small>комиссии {money(p.fees_rub)}</small></>}</td><td><b>{money(p.total_cash_outlay_rub)}</b><br/><small>{money(p.effective_cost_rub_per_native)} за 1 {p.credit_currency}</small></td><td><b>{num(p.consumed_native,6)} / {num(p.credit_native,6)}</b><br/><small>остаток {num(p.remaining_native,6)} {p.credit_currency}</small></td><td><b>{money(p.realized_revenue_rub)}</b><br/><small>{p.operations_count} операций</small></td><td className={Number(p.realized_profit_rub)>=0?styles.good:styles.bad}><b>{money(p.realized_profit_rub)}</b><br/><small>маржа {num(p.realized_margin_percent)}%</small></td></tr>)}</tbody></table>}
   </section>
   <div className={styles.notice}><b>Как считается публичная цена:</b> цена клиенту = закупка в ₽ × [1 + (наценка + налог + комиссия пополнения + прочие расходы + резерв возврата) / 100]. Историческая закупка и историческое списание не меняются задним числом.</div>
   <section className={styles.grid}>
    <div className={styles.card}><small>Выручка</small><strong>{money(data.summary.revenue_rub)}</strong></div>
    <div className={styles.card}><small>API-закупка по запросам</small><strong>{money(data.summary.nominal_provider_cost_rub)}</strong></div>
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
     <td><b>IN {money(econProfit(p.retail_input_per_million_rub,p.provider_input_per_million_rub))}</b><br/><b>OUT {money(econProfit(p.retail_output_per_million_rub,p.provider_output_per_million_rub))}</b><br/><small>после всех указанных расходов</small></td>
     <td className={(econMargin(p.retail_input_per_million_rub,p.provider_input_per_million_rub)<Number(p.minimum_margin_percent??0)||econMargin(p.retail_output_per_million_rub,p.provider_output_per_million_rub)<Number(p.minimum_margin_percent??0))?styles.bad:styles.good}><b>IN {num(econMargin(p.retail_input_per_million_rub,p.provider_input_per_million_rub))}%</b><br/><b>OUT {num(econMargin(p.retail_output_per_million_rub,p.provider_output_per_million_rub))}%</b><br/><small>floor {p.minimum_margin_percent}%</small></td>
    </>}
   </tr>)}</tbody></table></section>
   <section className={styles.section}><h2>Остатки API</h2><p>Здесь показывается внутренний закупочный остаток. Фактический provider balance, если API его отдаёт, отображается выше рядом с конкретным ключом.</p>{data.accounts.length===0?<p>Закупочные аккаунты пока не заведены.</p>:<table className={styles.table}><thead><tr><th>Провайдер</th><th>Аккаунт</th><th>Доступно</th><th>Потрачено</th></tr></thead><tbody>{data.accounts.map(a=><tr key={a.id}><td>{a.provider_name}</td><td>{a.label}</td><td>{num(a.available_native,4)} {a.currency}</td><td>{num(a.spent_native,4)} {a.currency}</td></tr>)}</tbody></table>}</section>
  </>}
 </>;
}
