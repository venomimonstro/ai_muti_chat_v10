"use client";

import {useEffect,useMemo,useState} from "react";
import {api} from "../../../../lib/api";
import styles from "../../admin.module.css";

type BudgetItem={scope_type:"vendor"|"model";scope_key:string;label:string;allocated_rub:string;spent_rub?:string;remaining_rub?:string};
type LedgerKey={
 id:string;provider:string;provider_name:string;label:string;masked:string;account_id:string|null;account_currency:string|null;is_default:boolean;
 model_scope_source?:string;allowed_models?:string[];budget_plan?:BudgetItem[];budget_status?:BudgetItem[];
};
type Purchase={
 id:string;document_number:string;state:"active"|"cancelled"|"deleted";provider_name:string;account_id:string;api_key_id:string|null;api_key_label:string;api_key_masked:string;
 credit_native:string;credit_currency:string;consumed_native:string;remaining_native:string;payment_amount:string|null;payment_currency:string;payment_fx_rate_rub:string|null;market_fx_rate_rub:string|null;
 base_cost_rub:string;fees_rub:string;total_cash_outlay_rub:string;effective_cost_rub_per_native:string;realized_revenue_rub:string;realized_profit_rub:string;realized_margin_percent:string;
 operations_count:number;reference:string;purchased_at:string;editable:boolean;cancellable:boolean;deletable:boolean;pricing_snapshot?:Record<string,unknown>;
};
type LedgerData={summary:{purchase_documents:number;cash_outlay_rub:string;fees_rub:string;realized_revenue_rub:string;realized_profit_rub:string;realized_margin_percent:string};keys:LedgerKey[];purchases:Purchase[]};
type PriceTier={conditions:string[];cost_rub:string};
type PolzaPrice={
 id:string;display_name:string;model_slug:string|null;configured:boolean;allowed?:boolean;currency:string;
 input_per_million:string|null;output_per_million:string|null;image_input_per_million:string|null;image_output_per_million:string|null;image_per_image:string|null;
 pricing_available?:boolean;pricing_source?:string;pricing_tiers?:PriceTier[];model_type?:string;
};
type PolzaRow=PolzaPrice&{markup_percent:string;budget_rub:string};
type Draft={
 api_key_id:string;credit_native:string;credit_currency:string;payment_amount:string;payment_currency:string;payment_fx_rate_rub:string;
 fees_rub:string;market_fx_rate_rub:string;purchased_at:string;reference:string;provider_markup_percent:string;
};

const emptyDraft=():Draft=>({
 api_key_id:"",credit_native:"",credit_currency:"USD",payment_amount:"",payment_currency:"RUB",payment_fx_rate_rub:"",
 fees_rub:"0",market_fx_rate_rub:"",purchased_at:new Date().toISOString().slice(0,10),reference:"",provider_markup_percent:"200"
});
const money=(v:string|number|null|undefined)=>`${Number(v??0).toFixed(2).replace(".",",")} ₽`;
const num=(v:string|number|null|undefined,d=6)=>Number(v??0).toFixed(d).replace(".",",");
const stateLabel={active:"Активен",cancelled:"Отменён",deleted:"Удалён"};

function vendorOf(modelId:string){return (modelId.split("/",1)[0]||"other").toLowerCase()}
function vendorLabel(vendor:string){
 const labels:Record<string,string>={openai:"GPT / OpenAI",anthropic:"Claude / Anthropic",google:"Gemini / Google","x-ai":"Grok / xAI",deepseek:"DeepSeek",qwen:"Qwen",mistralai:"Mistral"};
 return labels[vendor]||vendor;
}

export default function ProcurementOrdersPage(){
 const[data,setData]=useState<LedgerData|null>(null);
 const[draft,setDraft]=useState<Draft>(emptyDraft());
 const[editing,setEditing]=useState<Purchase|null>(null);
 const[polzaRows,setPolzaRows]=useState<PolzaRow[]>([]);
 const[vendorBudgets,setVendorBudgets]=useState<Record<string,string>>({});
 const[priceBusy,setPriceBusy]=useState(false);
 const[modelFilter,setModelFilter]=useState("");
 const[busy,setBusy]=useState(false);
 const[error,setError]=useState("");
 const[notice,setNotice]=useState("");

 const selectedKey=useMemo(()=>data?.keys.find(item=>item.id===draft.api_key_id)||null,[data,draft.api_key_id]);
 const isPolza=selectedKey?.provider==="polza";
 const visibleRows=polzaRows.filter(row=>!modelFilter||(`${row.display_name} ${row.id}`).toLowerCase().includes(modelFilter.toLowerCase()));
 const vendors=useMemo(()=>Array.from(new Set(polzaRows.map(row=>vendorOf(row.id)))).sort(),[polzaRows]);

 const load=async()=>{
  setBusy(true);
  try{
   const result=await api<LedgerData>("/admin/procurement/ledger/");
   setData(result);
   setDraft(v=>({...v,api_key_id:v.api_key_id||result.keys[0]?.id||""}));
   setError("");
  }catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить закупочные ордера")}
  finally{setBusy(false)}
 };
 useEffect(()=>{void load()},[]);
 const post=async<T=unknown>(payload:Record<string,unknown>)=>api<T>("/admin/procurement/ledger/",{method:"POST",body:JSON.stringify(payload)});

 const loadPolzaPricing=async(keyId:string)=>{
  const key=data?.keys.find(item=>item.id===keyId);
  if(!key||key.provider!=="polza"){setPolzaRows([]);setVendorBudgets({});return}
  setPriceBusy(true);
  try{
   const result=await post<{provider:string;models:PolzaPrice[]}>({action:"polza_pricing",api_key_id:keyId});
   const plan=key.budget_plan||[];
   const modelBudget=Object.fromEntries(plan.filter(x=>x.scope_type==="model").map(x=>[x.scope_key,x.allocated_rub]));
   const vendorBudget=Object.fromEntries(plan.filter(x=>x.scope_type==="vendor").map(x=>[x.scope_key,x.allocated_rub]));
   setVendorBudgets(vendorBudget);
   setPolzaRows(result.models.map(model=>({...model,markup_percent:"",budget_rub:modelBudget[model.id]||""})));
   setError("");
  }catch(e){
   setPolzaRows([]);
   setVendorBudgets({});
   setError(e instanceof Error?e.message:"Не удалось получить модели/цены Polza");
  }finally{setPriceBusy(false)}
 };

 const selectKey=async(keyId:string)=>{
  const key=data?.keys.find(item=>item.id===keyId);
  setDraft(v=>({...v,api_key_id:keyId,credit_currency:key?.provider==="polza"?"RUB":(key?.account_currency||v.credit_currency)}));
  setPolzaRows([]);setVendorBudgets({});
  if(key?.provider==="polza")await loadPolzaPricing(keyId);
 };

 useEffect(()=>{
  if(!data||editing||!draft.api_key_id||polzaRows.length>0)return;
  const key=data.keys.find(item=>item.id===draft.api_key_id);
  if(key?.provider==="polza")void loadPolzaPricing(key.id);
 },[data,draft.api_key_id,editing,polzaRows.length]);

 const updatePolzaRow=(id:string,patch:Partial<PolzaRow>)=>setPolzaRows(rows=>rows.map(row=>row.id===id?{...row,...patch}:row));

 const reset=()=>{
  setEditing(null);setPolzaRows([]);setVendorBudgets({});setModelFilter("");
  setDraft(v=>({...emptyDraft(),api_key_id:v.api_key_id,credit_currency:data?.keys.find(k=>k.id===v.api_key_id)?.provider==="polza"?"RUB":"USD"}));
 };

 const save=async()=>{
  if(!draft.credit_native||!draft.payment_amount||(!editing&&!draft.api_key_id)){setError("Заполните API-ключ, номинал и фактическую оплату");return}
  if(!editing&&isPolza&&polzaRows.length===0){setError("Polza-ключ не отдал синхронизированный список моделей. Нажмите «Проверить API» у ключа.");return}
  setBusy(true);setError("");setNotice("");
  try{
   if(editing){
    const p=await post<Purchase>({action:"edit_purchase",purchase_id:editing.id,...draft,base_cost_rub:"",payment_fx_rate_rub:draft.payment_currency==="RUB"?"":draft.payment_fx_rate_rub});
    setNotice(`Ордер ${p.document_number} изменён.`);
   }else{
    const polza_models=isPolza?polzaRows.map(row=>({
     upstream_model:row.id,display_name:row.display_name,currency:row.currency,
     input_per_million:row.input_per_million??"",output_per_million:row.output_per_million??"",
     image_input_per_million:row.image_input_per_million??"",image_output_per_million:row.image_output_per_million??"",
     image_per_image:row.image_per_image??"",markup_percent:row.markup_percent
    })):[];
    const budget_plan:BudgetItem[]=isPolza?[
     ...vendors.filter(v=>Number(vendorBudgets[v]||0)>0).map(v=>({scope_type:"vendor" as const,scope_key:v,label:vendorLabel(v),allocated_rub:vendorBudgets[v]})),
     ...polzaRows.filter(row=>Number(row.budget_rub||0)>0).map(row=>({scope_type:"model" as const,scope_key:row.id,label:row.display_name,allocated_rub:row.budget_rub}))
    ]:[];
    const p=await post<Purchase>({
     action:"purchase_key",...draft,base_cost_rub:"",
     payment_fx_rate_rub:draft.payment_currency==="RUB"?"":draft.payment_fx_rate_rub,
     polza_models,provider_markup_percent:isPolza?draft.provider_markup_percent:"",budget_plan
    });
    setNotice(`Ордер ${p.document_number} создан. Polza-моделей: ${isPolza?polzaRows.length:0}.`);
   }
   reset();await load();
  }catch(e){setError(e instanceof Error?e.message:"Не удалось сохранить ордер")}
  finally{setBusy(false)}
 };

 const startEdit=(p:Purchase)=>{
  setEditing(p);setPolzaRows([]);setVendorBudgets({});
  const s=(p.pricing_snapshot||{}) as Record<string,unknown>;
  setDraft({api_key_id:p.api_key_id||"",credit_native:p.credit_native,credit_currency:p.credit_currency,payment_amount:p.payment_amount||"",payment_currency:p.payment_currency,payment_fx_rate_rub:p.payment_fx_rate_rub||"",fees_rub:p.fees_rub,market_fx_rate_rub:p.market_fx_rate_rub||"",purchased_at:p.purchased_at.slice(0,10),reference:p.reference||"",provider_markup_percent:String(s.provider_markup_percent||"")});
  window.scrollTo({top:0,behavior:"smooth"});
 };

 const orderAction=async(p:Purchase,action:"cancel_purchase"|"delete_purchase")=>{
  if(!window.confirm(`${action==="cancel_purchase"?"Отменить":"Удалить"} закупочный ордер ${p.document_number}?`))return;
  setBusy(true);setError("");setNotice("");
  try{await post({action,purchase_id:p.id});if(editing?.id===p.id)reset();await load();}
  catch(e){setError(e instanceof Error?e.message:"Операция не выполнена")}
  finally{setBusy(false)}
 };

 const snapshotSummary=(p:Purchase)=>{
  const snapshot=(p.pricing_snapshot||{}) as Record<string,unknown>;
  const models=Array.isArray(snapshot.models)?snapshot.models as Record<string,unknown>[]:[];
  if(models.length)return <><br/><small>Polza: {models.length} моделей · наценка {String(snapshot.provider_markup_percent||"—")}%</small></>;
  return null;
 };

 return <>
  <header className={styles.header}><div><h1>Закупочные ордера API</h1><p>Баланс, себестоимость моделей, наценка и внутренние бюджеты по ключам.</p></div><button className={styles.button} disabled={busy} onClick={()=>void load()}>Обновить</button></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  {data&&<>
   <section className={styles.grid}>
    <div className={styles.card}><small>Активных ордеров</small><strong>{data.summary.purchase_documents}</strong></div>
    <div className={styles.card}><small>Вложено</small><strong>{money(data.summary.cash_outlay_rub)}</strong></div>
    <div className={styles.card}><small>Выручка</small><strong>{money(data.summary.realized_revenue_rub)}</strong></div>
    <div className={styles.card}><small>Прибыль</small><strong>{money(data.summary.realized_profit_rub)}</strong></div>
   </section>

   <section className={styles.section}>
    <div className={styles.header}><div><h2>{editing?`Изменить ${editing.document_number}`:"Новый закупочный ордер"}</h2><p>{editing?"Финансовые поля доступны до первого списания.":"Для Polza модели ключа и их цены подгружаются автоматически."}</p></div>{editing&&<button className={styles.button} onClick={reset}>Отменить</button>}</div>
    <div className={styles.filters}>
     <label>API-ключ<select disabled={!!editing} value={draft.api_key_id} onChange={e=>void selectKey(e.target.value)}><option value="">Выберите ключ</option>{data.keys.map(k=><option key={k.id} value={k.id}>{k.provider_name} · {k.label} · {k.masked}</option>)}</select></label>
     <label>Номинал ключа<input inputMode="decimal" value={draft.credit_native} onChange={e=>setDraft(v=>({...v,credit_native:e.target.value}))} placeholder="1500"/></label>
     <label>Валюта<select disabled={!!editing} value={draft.credit_currency} onChange={e=>setDraft(v=>({...v,credit_currency:e.target.value}))}><option>RUB</option><option>USD</option><option>EUR</option></select></label>
     <label>Фактически оплачено<input inputMode="decimal" value={draft.payment_amount} onChange={e=>setDraft(v=>({...v,payment_amount:e.target.value}))}/></label>
     <label>Валюта оплаты<select value={draft.payment_currency} onChange={e=>setDraft(v=>({...v,payment_currency:e.target.value}))}><option>RUB</option><option>USD</option><option>EUR</option></select></label>
     {draft.payment_currency!=="RUB"&&<label>Курс ₽<input inputMode="decimal" value={draft.payment_fx_rate_rub} onChange={e=>setDraft(v=>({...v,payment_fx_rate_rub:e.target.value}))}/></label>}
     <label>Комиссии, ₽<input inputMode="decimal" value={draft.fees_rub} onChange={e=>setDraft(v=>({...v,fees_rub:e.target.value}))}/></label>
     <label>Дата<input type="date" value={draft.purchased_at} onChange={e=>setDraft(v=>({...v,purchased_at:e.target.value}))}/></label>
     <label>Комментарий<input value={draft.reference} onChange={e=>setDraft(v=>({...v,reference:e.target.value}))}/></label>
    </div>

    {!editing&&isPolza&&<div style={{marginTop:16}}>
     <div className={styles.notice}><b>Polza.ai · модели ключа синхронизированы автоматически</b><br/><small>Источник: {selectedKey?.model_scope_source||"проверка ключа"}. Моделей: {polzaRows.length}. Повторно выбирать модели не нужно.</small></div>
     <div className={styles.filters}>
      <label>Общая наценка Polza, %<input inputMode="decimal" value={draft.provider_markup_percent} onChange={e=>setDraft(v=>({...v,provider_markup_percent:e.target.value}))}/></label>
      <label>Поиск модели<input value={modelFilter} onChange={e=>setModelFilter(e.target.value)} placeholder="GPT, Claude, Image…"/></label>
      <button type="button" className={styles.button} disabled={priceBusy} onClick={()=>draft.api_key_id&&void loadPolzaPricing(draft.api_key_id)}>{priceBusy?"Синхронизация…":"Обновить модели и цены"}</button>
     </div>

     {vendors.length>0&&<><h3>Бюджеты внутри ключа</h3><div className={styles.grid}>{vendors.map(v=><div className={styles.card} key={v}><small>{vendorLabel(v)}</small><strong>{money(vendorBudgets[v]||0)}</strong><input inputMode="decimal" value={vendorBudgets[v]||""} onChange={e=>setVendorBudgets(x=>({...x,[v]:e.target.value}))} placeholder="Бюджет, ₽"/>{selectedKey?.budget_status?.find(x=>x.scope_type==="vendor"&&x.scope_key===v)&&<small>потрачено {money(selectedKey.budget_status.find(x=>x.scope_type==="vendor"&&x.scope_key===v)?.spent_rub)} · осталось {money(selectedKey.budget_status.find(x=>x.scope_type==="vendor"&&x.scope_key===v)?.remaining_rub)}</small>}</div>)}</div></>}

     {priceBusy?<p>Загружаем…</p>:<div style={{overflowX:"auto"}}><table className={styles.table}>
      <thead><tr><th>Модель ключа</th><th>IN / 1M</th><th>OUT / 1M</th><th>Image IN</th><th>Image OUT</th><th>За изображение</th><th>Своя наценка %</th><th>Бюджет модели ₽</th></tr></thead>
      <tbody>{visibleRows.map(row=><tr key={row.id}>
       <td><b>{row.display_name}</b><br/><small>{row.id}</small><br/><small>{row.pricing_available===false?"цена вручную":"Polza auto"}</small></td>
       <td><input style={{width:105}} inputMode="decimal" value={row.input_per_million??""} onChange={e=>updatePolzaRow(row.id,{input_per_million:e.target.value})}/></td>
       <td><input style={{width:105}} inputMode="decimal" value={row.output_per_million??""} onChange={e=>updatePolzaRow(row.id,{output_per_million:e.target.value})}/></td>
       <td><input style={{width:105}} inputMode="decimal" value={row.image_input_per_million??""} onChange={e=>updatePolzaRow(row.id,{image_input_per_million:e.target.value})}/></td>
       <td><input style={{width:105}} inputMode="decimal" value={row.image_output_per_million??""} onChange={e=>updatePolzaRow(row.id,{image_output_per_million:e.target.value})}/></td>
       <td><input style={{width:105}} inputMode="decimal" value={row.image_per_image??""} onChange={e=>updatePolzaRow(row.id,{image_per_image:e.target.value})}/></td>
       <td><input style={{width:90}} inputMode="decimal" value={row.markup_percent} onChange={e=>updatePolzaRow(row.id,{markup_percent:e.target.value})} placeholder="общая"/></td>
       <td><input style={{width:105}} inputMode="decimal" value={row.budget_rub} onChange={e=>updatePolzaRow(row.id,{budget_rub:e.target.value})} placeholder="необязательно"/></td>
      </tr>)}</tbody>
     </table></div>}
    </div>}

    <div className={styles.actions} style={{marginTop:16}}><button className={`${styles.button} ${styles.primary}`} disabled={busy||data.keys.length===0} onClick={()=>void save()}>{editing?"Сохранить":"Провести закупку"}</button></div>
   </section>

   <section className={styles.section}><h2>Журнал закупок</h2>{data.purchases.length===0?<p>Закупок пока нет.</p>:<table className={styles.table}><thead><tr><th>Документ</th><th>Статус</th><th>Ключ</th><th>Куплено</th><th>Себестоимость</th><th>FIFO</th><th>Прибыль</th><th></th></tr></thead><tbody>{data.purchases.map(p=><tr key={p.id}>
    <td><b>{p.document_number}</b><br/><small>{new Date(p.purchased_at).toLocaleDateString("ru-RU")}</small></td><td>{stateLabel[p.state]}</td>
    <td><b>{p.provider_name}</b><br/>{p.api_key_label}{snapshotSummary(p)}</td><td>{num(p.credit_native)} {p.credit_currency}</td>
    <td>{money(p.total_cash_outlay_rub)}</td><td>{num(p.consumed_native)} / {num(p.credit_native)}</td><td>{money(p.realized_profit_rub)}</td>
    <td><div className={styles.actions}>{p.editable&&<button className={styles.button} onClick={()=>startEdit(p)}>Изменить</button>}{p.cancellable&&<button className={styles.button} onClick={()=>void orderAction(p,"cancel_purchase")}>Отменить</button>}{p.deletable&&<button className={`${styles.button} ${styles.danger}`} onClick={()=>void orderAction(p,"delete_purchase")}>Удалить</button>}</div></td>
   </tr>)}</tbody></table>}</section>
  </>}
 </>;
}
