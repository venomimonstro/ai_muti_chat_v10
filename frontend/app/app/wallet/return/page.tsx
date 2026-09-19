"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {api} from "../../../../lib/api";
import type {Wallet} from "../../../../lib/types";
import {trackProductEvent} from "../../../components/ProductAnalytics";
import styles from "../wallet.module.css";

type Payment={id:string;amount_rub:string;status:string;receipt_status:string;created_at:string};
const paymentStatus:Record<string,string>={created:"Создан",pending:"Ожидает подтверждения",succeeded:"Оплачен",canceled:"Отменён"};
const receiptStatus:Record<string,string>={not_required:"Не требуется",pending:"Ожидается",succeeded:"Сформирован",failed:"Ошибка",legal_review:"Нужна проверка"};

export default function PaymentReturnPage(){
 const [payment,setPayment]=useState<Payment|null>(null);
 const [wallet,setWallet]=useState<Wallet|null>(null);
 const [error,setError]=useState("");
 useEffect(()=>{
  let cancelled=false;
  let tries=0;
  const check=async()=>{
   tries+=1;
   try{
    let payments=await api<Payment[]>("/payments/");
    const pendingId=sessionStorage.getItem("pending-payment-id");
    let current=(pendingId?payments.find(item=>item.id===pendingId):null)??payments[0]??null;
    if(current&&!["succeeded","canceled"].includes(current.status)){
     try{current=await api<Payment>(`/payments/${current.id}/sync/`,{method:"POST",body:"{}"});}
     catch(syncError){if(tries>=8)throw syncError;}
    }
    const w=await api<Wallet>("/wallet/");
    if(cancelled)return;
    setPayment(current);setWallet(w);setError("");
    if(current&&["succeeded","canceled"].includes(current.status)){
     const marker=`payment-outcome:${current.id}`;
     if(!sessionStorage.getItem(marker)){
      sessionStorage.setItem(marker,"1");
      void trackProductEvent(current.status==="succeeded"?"payment_success":"payment_failed",{payment_id:current.id,amount_rub:current.amount_rub});
     }
     sessionStorage.removeItem("pending-payment-id");
    }else if(current&&tries<8){
     window.setTimeout(()=>void check(),1800);
    }
   }catch(reason){
    if(!cancelled)setError(reason instanceof Error?reason.message:"Не удалось проверить платёж");
   }
  };
  void check();
  return()=>{cancelled=true};
 },[]);
 return <main className={styles.page}><div className={styles.shell}><header className={styles.top}><div><h1>Статус пополнения</h1><p>Проверяем подтверждение YooKassa и обновляем баланс.</p></div><Link href="/app/wallet">← Баланс</Link></header>{error&&<div className={`${styles.notice} ${styles.error}`}>{error}</div>}<section className={styles.card}>{!payment?<p>Платёж пока не найден. Обновите страницу через несколько секунд.</p>:<><h2>{payment.status==="succeeded"?"Баланс пополнен":payment.status==="canceled"?"Платёж отменён":"Платёж обрабатывается"}</h2><div className={styles.row}><span>Сумма</span><b>{Number(payment.amount_rub).toFixed(2).replace(".",",")} ₽</b></div><div className={styles.row}><span>Статус</span><b>{paymentStatus[payment.status]??"Обрабатывается"}</b></div><div className={styles.row}><span>Чек</span><b>{receiptStatus[payment.receipt_status]??"Проверяется"}</b></div><div className={styles.row}><span>Текущий баланс</span><b>{Number(wallet?.available_rub??0).toFixed(2).replace(".",",")} ₽</b></div></>}<div style={{marginTop:18}}><Link className={`${styles.button} ${styles.primary}`} href="/app">Вернуться в рабочее пространство</Link></div></section></div></main>
}
