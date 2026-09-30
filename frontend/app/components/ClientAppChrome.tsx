"use client";

import Link from "next/link";
import {usePathname} from "next/navigation";
import {useEffect,useState,type ReactNode} from "react";
import {Icon} from "../workspace/Icons";

const primary=[
 {href:"/app",label:"Чат",icon:"plus" as const,mobile:true},
 {href:"/app/projects",label:"Проекты",icon:"folder" as const,mobile:true},
 {href:"/app/agents",label:"Агенты",icon:"brain" as const,mobile:true},
 {href:"/app/smm",label:"SMM Studio",icon:"spark" as const,mobile:true},
 {href:"/app/dev",label:"Dev Studio",icon:"zap" as const,mobile:false},
 {href:"/app/images",label:"Изображения",icon:"spark" as const,mobile:false},
 {href:"/app/compare",label:"Сравнение",icon:"scale" as const,mobile:false},
];
const account=[
 {href:"/app/integrations",label:"Интеграции",icon:"settings" as const},
 {href:"/app/usage",label:"Использование",icon:"brain" as const},
 {href:"/app/wallet",label:"Баланс",icon:"wallet" as const},
 {href:"/app/account",label:"Аккаунт",icon:"user" as const},
 {href:"/app/settings",label:"Настройки",icon:"settings" as const},
 {href:"/app/help",label:"Помощь",icon:"search" as const},
];
const moreItems=[...primary.filter(item=>!item.mobile),...account];

export default function ClientAppChrome({children}:{children:ReactNode}){
 const pathname=usePathname();const[moreOpen,setMoreOpen]=useState(false);
 useEffect(()=>setMoreOpen(false),[pathname]);
 if(pathname==="/app")return <>{children}</>;
 const active=(href:string)=>href==="/app"?pathname===href:pathname===href||pathname.startsWith(`${href}/`);
 return <div className="clientChrome">
  <aside className="clientChromeNav" aria-label="Личный кабинет">
   <Link href="/app" className="clientChromeBrand"><span/>AI Workspace</Link>
   <nav className="clientChromePrimary">{primary.map(item=><Link key={item.href} href={item.href} aria-label={item.label} className={`${active(item.href)?"active":""} ${item.mobile?"":"clientChromeMobileSecondary"}`}><Icon name={item.icon} size={17}/><span>{item.label}</span></Link>)}</nav>
   <div className="clientChromeDivider"/>
   <nav className="clientChromeAccount">{account.map(item=><Link key={item.href} href={item.href} aria-label={item.label} className={active(item.href)?"active":""}><Icon name={item.icon} size={17}/><span>{item.label}</span></Link>)}</nav>
   <button type="button" className={`clientChromeMoreButton ${moreOpen?"active":""}`} onClick={()=>setMoreOpen(value=>!value)} aria-label="Ещё разделы" aria-expanded={moreOpen}><Icon name="settings" size={18}/><span>Ещё</span></button>
  </aside>
  <div className="clientChromeMain">{children}</div>
  {moreOpen&&<div className="clientChromeMoreLayer" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)setMoreOpen(false)}}><section className="clientChromeMoreSheet" role="dialog" aria-modal="true" aria-labelledby="client-more-title"><header><div><span>AI WORKSPACE</span><h2 id="client-more-title">Ещё разделы</h2></div><button type="button" onClick={()=>setMoreOpen(false)} aria-label="Закрыть">×</button></header><nav>{moreItems.map(item=><Link key={item.href} href={item.href} className={active(item.href)?"active":""}><Icon name={item.icon} size={18}/><span>{item.label}</span><b>→</b></Link>)}</nav></section></div>}
 </div>;
}
