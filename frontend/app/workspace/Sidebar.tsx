"use client";

import Link from "next/link";
import type {Conversation,Wallet} from "../../lib/types";

export function Sidebar({items,activeId,wallet,onCreate,onSelect}:{items:Conversation[];activeId:string|null;wallet:Wallet|null;onCreate:()=>void;onSelect:(id:string)=>void}){
  return <aside className="proSidebar"><Link className="proBrand" href="/">AI Workspace</Link><button className="proNew" onClick={onCreate}>＋ Новый чат</button><nav><Link href="/app">Чаты</Link><Link href="/app/account">Аккаунт</Link><Link href="/app/wallet">Баланс</Link><Link href="/app/legacy">Расширенные инструменты</Link></nav><div className="proHistory"><small>НЕДАВНИЕ ЧАТЫ</small>{items.map(item=><button key={item.id} className={item.id===activeId?"active":""} onClick={()=>onSelect(item.id)}><span>{item.title}</span><time>{new Date(item.updated_at).toLocaleDateString("ru")}</time></button>)}</div><Link className="proWallet" href="/app/wallet"><small>Доступно</small><b>{Number(wallet?.available_rub??0).toFixed(2).replace(".",",")} ₽</b></Link></aside>;
}
