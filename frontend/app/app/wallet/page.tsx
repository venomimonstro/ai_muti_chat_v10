"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {useRouter} from "next/navigation";
import {ApiError,api} from "../../../lib/api";
import type {Wallet} from "../../../lib/types";
import {trackProductEvent} from "../../components/ProductAnalytics";
import styles from "./wallet.module.css";

type Payment={id:string;amount_rub:string;currency:string;status:"created"|"pending"|"succeeded"|"canceled";confirmation_url:string;receipt_status:string;credited_at:string|null;created_at:string};
type RefundRequest={id:string;payment_id:string;refund_id:string|null;amount_rub:string;reason:string;status:string;admin_comment:string;held_at:string|null;resolved_at:string|null;created_at:string;updated_at:string};
const money=(value:string|number|null|undefined)=>`${Number(value??0).toFixed(2).replace(".",",")} ₽`;
const paymentStatus:Record<string,string>={created:"Создан",pending:"Ожидает оплаты",succeeded:"Оплачен",canceled:"Отменён"};
const receiptStatus:Record<string,string>={not_required:"Не требуется",pending:"Ожидается",succeeded:"Сформирован",failed:"Ошибка",legal_review:"Нужна проверка"};
const refundStatus:Record<string,string>={pending:"На рассмотрении",approved:"Одобрен",processing:"Возврат выполняется",succeeded:"Возвращён",rejected:"Отклонён",failed:"Ошибка возврата"};
const ledgerKind:Record<string,string>={credit:"Пополнение",reserve:"Резерв",release:"Освобождение резерва",debit:"Списание",refund:"Возврат",adjustment:"Корректировка"};
const topupStorageKey=(amount:string)=>`pending-topup-key:${amount}`;
const stableTopupKey=(amount:string)=>{const storageKey=topupStorageKey(amount);const existing=window.sessionStorage.getItem(storageKey);if(existing)return existing;const created=`wallet:${crypto.randomUUID()}`;window.sessionStorage.setItem(storageKey,created);return created;};
const refundRequestStorageKey=(paymentId:string,amount:string)=>`pending-refund-request-key:${paymentId}:${amount}`;
const stableRefundRequestKey=(paymentId:string,amount:string)=>{const storageKey=refundRequestStorageKey(paymentId,amount);const existing=window.sessionStorage.getItem(storageKey);if(existing)return existing;const created=`refund-request:${crypto.randomUUID()}`;window.sessionStorage.setItem(storageKey,created);return created;};

export default function WalletPage(){
 const router=useRouter();
 const [wallet,setWallet]=useState<Wallet|null>(null);
 const [payments,setPayments]=useState<Payment[]>([]);
 const [refunds,setRefunds]=useState<RefundRequest[]>([]);
 const [amount,setAmount]=useState("500");
 const [refundPayment,setRefundPayment]=useState("");
 const [refundAmount,setRefundAmount]=useState("");
 const [refundReason,setRefundReason]=useState("");
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");

 const load=async()=>{try{const[w,p,r]=await Promise.all([api<Wallet>("/wallet/"),api<Payment[]>("/payments/"),api<RefundRequest[]>("/refund-requests/")]);setWallet(w);setPayments(p);setRefunds(r);if(!refundPayment){const latest=p.find(item=>item.status==="succeeded");if(latest)setRefundPayment(latest.id);}}catch(reason){if(reason instanceof ApiError&&[401,403].includes(reason.status))router.replace("/login");else setError(reason instanceof Error?reason.message:"Не удалось загрузить кошелёк");}};
 useEffect(()=>{void load();},[]);

 const topup=async()=>{setBusy(true);setError("");const requestedAmount=amount;const key=stableTopupKey(requestedAmount);try{const payment=await api<Payment>("/payments/",{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({amount_rub:requestedAmount})});sessionStorage.removeItem(topupStorageKey(requestedAmount));void trackProductEvent("payment_started",{amount_rub:requestedAmount,payment_id:payment.id});sessionStorage.setItem("pending-payment-id",payment.id);if(payment.confirmation_url)window.location.assign(payment.confirmation_url);else{await load();}}catch(reason){void trackProductEvent("payment_failed",{amount_rub:requestedAmount,stage:"create"});setError(`${reason instanceof Error?reason.message:"Не удалось создать платёж"} Если статус платёжного провайдера не определён, повторное нажатие безопасно продолжит ту же операцию и не создаст второй платёж.`);}finally{setBusy(false);}};

 const requestRefund=async()=>{if(!refundPayment||Number(refundAmount)<1)return;setBusy(true);setError("");const paymentId=refundPayment;const requestedAmount=refundAmount;const key=stableRefundRequestKey(paymentId,requestedAmount);try{await api<RefundRequest>("/refund-requests/",{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify({payment_id:paymentId,amount_rub:requestedAmount,reason:refundReason})});sessionStorage.removeItem(refundRequestStorageKey(paymentId,requestedAmount));setRefundAmount("");setRefundReason("");await load();}catch(reason){setError(`${reason instanceof Error?reason.message:"Не удалось отправить заявку на возврат"} Повторная отправка использует тот же ключ операции.`);}finally{setBusy(false);}};
 const successfulPayments=payments.filter(item=>item.status==="succeeded");

 return <main className={styles.page}><div className={styles.shell}>
  <header className={styles.top}><div><h1>Баланс и платежи</h1><p>Один рублёвый баланс для AI-запросов и функций сервиса.</p></div><Link href="/app">← Рабочее пространство</Link></header>
  {error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}
  <div className={styles.notice} style={{marginBottom:16}}><b>Только разовое пополнение.</b> Сервис не сохраняет карту для автоматических списаний и не подключает автопродление. Новый платёж создаётся только после вашего нажатия «Пополнить».</div>
  <section className={styles.hero}><div className={styles.balance}><small>Доступно</small><strong>{money(wallet?.available_rub)}</strong><div className={styles.split}><span>Оплачено: {money(wallet?.paid_rub)}</span><span>Промо: {money(wallet?.promo_rub)}</span><span>В резерве: {money(wallet?.reserved_rub)}</span></div><small style={{display:"block",marginTop:10}}>Во время AI-запроса максимальная сумма временно резервируется. После завершения списывается фактическая стоимость, остаток сразу возвращается. При ошибке до подтверждённого расхода LLM резерв освобождается полностью. Если расход провайдера уже подтверждён, списывается только рассчитанная по зафиксированному тарифу стоимость и никогда не больше заранее зарезервированного максимума.</small></div><div className={styles.card}><h2>Пополнить</h2><div className={styles.amounts}>{[300,500,1000,3000].map(item=><button key={item} className={amount===String(item)?styles.active:""} onClick={()=>setAmount(String(item))}>{item.toLocaleString("ru-RU")} ₽</button>)}</div><input className={styles.input} type="number" min="100" max="100000" step="1" value={amount} onChange={e=>setAmount(e.target.value)}/><button className={`${styles.button} ${styles.primary}`} disabled={busy||Number(amount)<100} onClick={topup}>{busy?"Обрабатываем…":`Пополнить на ${money(amount)}`}</button><small style={{display:"block",marginTop:9,color:"#777"}}>Это не подписка. Сумма не будет списана повторно автоматически.</small></div></section>

  <section className={styles.grid}>
   <div className={styles.card}><h2>История платежей</h2><div className={styles.list}>{payments.length===0?<p>Платежей пока нет.</p>:payments.map(item=><div className={styles.row} key={item.id}><div><b>{money(item.amount_rub)}</b><small>{new Date(item.created_at).toLocaleString("ru-RU")} · чек: {receiptStatus[item.receipt_status]??"Проверяется"}</small></div><span className={`${styles.status} ${item.status==="succeeded"?styles.ok:item.status==="canceled"?styles.bad:""}`}>{paymentStatus[item.status]??"Обрабатывается"}</span></div>)}</div></div>
   <div className={styles.card}><h2>Последние списания</h2><div className={styles.list}>{wallet?.entries.length?<>{wallet.entries.slice(0,30).map(entry=><div className={styles.row} key={entry.id}><div><b>{ledgerKind[entry.kind]??"Операция"}</b><small>{new Date(entry.created_at).toLocaleString("ru-RU")}</small></div><strong>{money(entry.amount_rub)}</strong></div>)}</>:<p>Операций пока нет.</p>}</div></div>
  </section>

  <section className={styles.grid}>
   <div className={styles.card}><h2>Запросить возврат</h2><p>Можно вернуть только неиспользованный оплаченный баланс. После отправки заявки указанная сумма временно исключается из доступного баланса, чтобы её нельзя было одновременно потратить.</p>{successfulPayments.length===0?<p>Нет успешных платежей, доступных для возврата.</p>:<><label>Платёж</label><select className={styles.input} value={refundPayment} onChange={e=>setRefundPayment(e.target.value)}>{successfulPayments.map(item=><option key={item.id} value={item.id}>{money(item.amount_rub)} · {new Date(item.created_at).toLocaleDateString("ru-RU")}</option>)}</select><label>Сумма возврата</label><input className={styles.input} type="number" min="1" step="0.01" value={refundAmount} onChange={e=>setRefundAmount(e.target.value)} placeholder="Например, 500"/><label>Причина</label><textarea className={styles.input} rows={3} value={refundReason} onChange={e=>setRefundReason(e.target.value)} placeholder="Кратко укажите причину"/><button className={`${styles.button} ${styles.primary}`} disabled={busy||!refundPayment||Number(refundAmount)<1} onClick={requestRefund}>Отправить заявку</button></>}</div>
   <div className={styles.card}><h2>Заявки на возврат</h2><div className={styles.list}>{refunds.length===0?<p>Заявок пока нет.</p>:refunds.map(item=><div className={styles.row} key={item.id}><div><b>{money(item.amount_rub)}</b><small>{new Date(item.created_at).toLocaleString("ru-RU")}{item.admin_comment?` · ${item.admin_comment}`:""}</small></div><span className={`${styles.status} ${item.status==="succeeded"?styles.ok:["rejected","failed"].includes(item.status)?styles.bad:""}`}>{refundStatus[item.status]??item.status}</span></div>)}</div></div>
  </section>
 </div></main>;
}
