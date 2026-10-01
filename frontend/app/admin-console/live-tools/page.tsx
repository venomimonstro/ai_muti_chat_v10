"use client";

import {useEffect,useState} from "react";
import {api} from "../../../lib/api";
import styles from "../admin.module.css";

type YandexSettings={
 configured:boolean;
 credential_source:string;
 api_key_configured:boolean;
 api_key_masked:string;
 api_key_health?:string;
 api_key_error?:string;
 provider_enabled?:boolean;
 folder_id:string;
 region:string;
 search_type:string;
 health_state:string;
 last_checked_at:string|null;
 endpoint:string;
 billing_ready?:boolean;
 customer_traffic_enabled?:boolean;
 available_requests?:string;
 acquisition_cost_rub_per_request?:string;
 markup_percent?:string;
 billing_policy?:string;
};
type Settings={
 yandex_search:YandexSettings;
 clock:{configured:boolean;source:string};
 weather:{configured:boolean;source:string};
};
type CheckItem={ok:boolean;error?:string;data?:Record<string,unknown>;result_count?:number;first?:{title:string;url:string}|null;billing?:string};
type CheckResult={ok:boolean;checks:Record<string,CheckItem>};

const healthLabel:Record<string,string>={healthy:"Работает",unknown:"Не проверен",degraded:"Ошибка",disabled:"Отключён"};
const rub=(value:string|undefined)=>{const number=Number(value??0);return Number.isFinite(number)?number.toLocaleString("ru-RU",{minimumFractionDigits:4,maximumFractionDigits:4}):String(value??"0")};

export default function LiveToolsAdmin(){
 const[data,setData]=useState<Settings|null>(null);
 const[apiKey,setApiKey]=useState("");
 const[folderId,setFolderId]=useState("");
 const[region,setRegion]=useState("225");
 const[searchType,setSearchType]=useState("SEARCH_TYPE_RU");
 const[markupPercent,setMarkupPercent]=useState("100");
 const[purchasedRequests,setPurchasedRequests]=useState("");
 const[purchaseCostRub,setPurchaseCostRub]=useState("");
 const[busy,setBusy]=useState("");
 const[error,setError]=useState("");
 const[notice,setNotice]=useState("");
 const[checks,setChecks]=useState<CheckResult|null>(null);

 const load=async()=>{try{const result=await api<Settings>("/admin/live-tools/");setData(result);setFolderId(result.yandex_search.folder_id||"");setRegion(result.yandex_search.region||"225");setSearchType(result.yandex_search.search_type||"SEARCH_TYPE_RU");setMarkupPercent(result.yandex_search.markup_percent||"100");setError("");}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось загрузить настройки live-инструментов");}};
 useEffect(()=>{void load();},[]);

 const save=async()=>{setBusy("save");setError("");setNotice("");try{const payload:Record<string,string>={folder_id:folderId.trim(),region:region.trim()||"225",search_type:searchType,markup_percent:markupPercent.trim()||"100"};if(apiKey.trim())payload.api_key=apiKey.trim();if(purchasedRequests.trim())payload.purchased_requests=purchasedRequests.trim();if(purchaseCostRub.trim())payload.purchase_cost_rub=purchaseCostRub.trim();const result=await api<Settings>("/admin/live-tools/",{method:"PATCH",body:JSON.stringify(payload)});setData(result);setApiKey("");setPurchasedRequests("");setPurchaseCostRub("");setNotice("Yandex Search сохранён. Ключ защищён, закупка учтена в procurement ledger, а стоимость поиска будет добавляться к ответу только при фактическом использовании.");}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось сохранить Yandex Search");}finally{setBusy("");}};

 const check=async(kind:"all"|"time"|"weather"|"yandex")=>{setBusy(`check:${kind}`);setError("");setNotice("");try{const result=await api<CheckResult>("/admin/live-tools/check/",{method:"POST",body:JSON.stringify({kind})});setChecks(result);setNotice(result.ok?"Проверка пройдена: инструменты отвечают.":"Проверка завершилась с ошибками — детали показаны ниже.");await load();}catch(reason){setChecks(null);setError(reason instanceof Error?reason.message:"Проверка live-инструментов не выполнена");await load();}finally{setBusy("");}};

 return <>
  <header className={styles.header}><div><h1>Интернет и live-данные</h1><p>Текущее время, погода и поиск в интернете. Эти данные получает сервер, а не придумывает LLM.</p></div><button className={styles.button} disabled={!!busy} onClick={()=>void load()}>Обновить</button></header>
  {notice&&<div className={styles.notice}>{notice}</div>}{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}

  <div className={styles.grid}>
   <div className={styles.card}><small>Точное время</small><strong>{data?.clock.configured?"Включено":"Ошибка"}</strong><p>Источник: серверное время + часовой пояс города.</p></div>
   <div className={styles.card}><small>Погода</small><strong>{data?.weather.configured?"Включено":"Ошибка"}</strong><p>Источник: {data?.weather.source||"live weather API"}.</p></div>
   <div className={styles.card}><small>Yandex Search</small><strong>{data?.yandex_search.customer_traffic_enabled?"Готов к трафику":data?.yandex_search.configured?"Ключ настроен":"Не настроен"}</strong><p>{data?.yandex_search.api_key_masked?`Ключ: ${data.yandex_search.api_key_masked}`:"API-ключ ещё не сохранён"}</p></div>
   <div className={styles.card}><small>Закупочный остаток</small><strong>{Number(data?.yandex_search.available_requests??0).toLocaleString("ru-RU")} запросов</strong><p>{data?.yandex_search.billing_ready?`Себестоимость: ${rub(data.yandex_search.acquisition_cost_rub_per_request)} ₽ / запрос` : "Добавьте закупку запросов, чтобы платный поиск участвовал в клиентском маршруте."}</p></div>
   <div className={styles.card}><small>Статус поиска</small><strong>{healthLabel[data?.yandex_search.health_state||"unknown"]||data?.yandex_search.health_state||"Не проверен"}</strong><p>{data?.yandex_search.last_checked_at?`Последняя проверка: ${new Date(data.yandex_search.last_checked_at).toLocaleString("ru-RU")}`:"Проверка ещё не выполнялась"}</p></div>
   <div className={styles.card}><small>Продажная наценка</small><strong>{data?.yandex_search.markup_percent??markupPercent}%</strong><p>Применяется к фактической закупочной себестоимости одного поискового вызова.</p></div>
  </div>

  <section className={styles.section}>
   <div className={styles.header}><div><h2>Yandex Search API</h2><p>Ключ хранится зашифрованно. Поиск сначала старается использовать бесплатный SearXNG; Yandex подключается по политике качества/актуальности и учитывается как отдельный расход инструмента.</p></div></div>
   <div className={styles.two}>
    <label>API-ключ Yandex Search<br/><input style={{width:"100%"}} type="password" autoComplete="new-password" placeholder={data?.yandex_search.api_key_configured?"Оставьте пустым, чтобы не менять сохранённый ключ":"Вставьте API-ключ"} value={apiKey} onChange={e=>setApiKey(e.target.value)}/></label>
    <label>Folder ID Yandex Cloud<br/><input style={{width:"100%"}} placeholder="b1g..." value={folderId} onChange={e=>setFolderId(e.target.value)}/></label>
    <label>Регион поиска<br/><input style={{width:"100%"}} value={region} onChange={e=>setRegion(e.target.value)}/><small>225 = Россия. Можно указать другой числовой ID региона Яндекса.</small></label>
    <label>Тип поиска<br/><select style={{width:"100%"}} value={searchType} onChange={e=>setSearchType(e.target.value)}><option value="SEARCH_TYPE_RU">Россия</option><option value="SEARCH_TYPE_COM">Международный</option><option value="SEARCH_TYPE_TR">Турция</option></select></label>
    <label>Наценка на поиск, %<br/><input style={{width:"100%"}} inputMode="decimal" value={markupPercent} onChange={e=>setMarkupPercent(e.target.value)}/><small>Итоговая цена всё равно проходит общий minimum margin guard.</small></label>
   </div>
   <h3 style={{marginTop:20}}>Добавить закупку</h3>
   <p>Заполняйте эти два поля только при новой оплате Яндекс-поиска. После сохранения они очистятся, поэтому одна закупка не будет начислена повторно.</p>
   <div className={styles.two}>
    <label>Куплено поисковых запросов<br/><input style={{width:"100%"}} inputMode="decimal" placeholder="1000" value={purchasedRequests} onChange={e=>setPurchasedRequests(e.target.value)}/></label>
    <label>Фактически оплачено, ₽<br/><input style={{width:"100%"}} inputMode="decimal" placeholder="1500" value={purchaseCostRub} onChange={e=>setPurchaseCostRub(e.target.value)}/></label>
   </div>
   <div className={styles.actions} style={{marginTop:16}}><button className={`${styles.button} ${styles.primary}`} disabled={!!busy||!folderId.trim()} onClick={()=>void save()}>{busy==="save"?"Сохраняем…":"Сохранить настройки / закупку"}</button><button className={styles.button} disabled={!!busy||!data?.yandex_search.customer_traffic_enabled} onClick={()=>void check("yandex")}>Проверить Яндекс-поиск (−1 запрос)</button></div>
  </section>

  <section className={styles.section}>
   <h2>Проверка инструментов</h2><p>Проверка идёт напрямую с production backend и не использует знания LLM. Проверка Yandex Search делает один реальный запрос и учитывает его как служебный закупочный расход.</p>
   <div className={styles.actions}><button className={styles.button} disabled={!!busy} onClick={()=>void check("time")}>Проверить время</button><button className={styles.button} disabled={!!busy} onClick={()=>void check("weather")}>Проверить погоду</button><button className={`${styles.button} ${styles.primary}`} disabled={!!busy} onClick={()=>void check("all")}>Проверить всё</button></div>
   {checks&&<table className={styles.table} style={{marginTop:16}}><thead><tr><th>Инструмент</th><th>Статус</th><th>Результат</th></tr></thead><tbody>{Object.entries(checks.checks).map(([name,item])=><tr key={name}><td>{name}</td><td className={item.ok?styles.good:styles.bad}>{item.ok?"Работает":"Ошибка"}</td><td>{item.error||item.first?.url||item.billing||JSON.stringify(item.data||{})}</td></tr>)}</tbody></table>}
  </section>
 </>;
}
