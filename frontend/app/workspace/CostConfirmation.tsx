"use client";

import {useEffect,useRef} from "react";

export type CostEstimate={estimated_max_rub:string;estimated_llm_max_rub?:string;estimated_search_max_rub?:string};

export function CostConfirmation({estimate,onConfirm,onCancel}:{estimate:CostEstimate|null;onConfirm:()=>void;onCancel:()=>void}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const dialog=ref.current;if(!estimate||!dialog)return;const previous=document.activeElement as HTMLElement|null;dialog.showModal();return()=>{dialog.close();previous?.focus()}},[estimate]);
 if(!estimate)return null;
 const amount=Number(estimate.estimated_max_rub).toLocaleString("ru-RU",{minimumFractionDigits:2,maximumFractionDigits:4});
 return <dialog ref={ref} className="chatCostDialog" aria-labelledby="chat-cost-title" onCancel={event=>{event.preventDefault();onCancel()}} onClick={event=>{if(event.target===event.currentTarget){const rect=event.currentTarget.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)onCancel()}}}>
  <h2 id="chat-cost-title">Подтвердить расход</h2><p>Этот запрос может стоить до</p><strong>{amount} ₽</strong><p>Спишем фактическую стоимость ответа и платного поиска. Неиспользованный резерв вернётся на баланс.</p>
  <footer><button type="button" onClick={onCancel} autoFocus>Отмена</button><button type="button" className="dialogPrimary" onClick={onConfirm}>Отправить запрос</button></footer>
 </dialog>;
}
