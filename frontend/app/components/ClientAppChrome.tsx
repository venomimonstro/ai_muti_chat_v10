"use client";

import Link from "next/link";
import {usePathname} from "next/navigation";
import type {ReactNode} from "react";
import {Icon} from "../workspace/Icons";

const primary=[
 {href:"/app",label:"Чат",icon:"plus" as const},
 {href:"/app/projects",label:"Проекты",icon:"folder" as const},
 {href:"/app/images",label:"Изображения",icon:"spark" as const},
 {href:"/app/compare",label:"Сравнение",icon:"scale" as const},
 {href:"/app/development",label:"Разработка",icon:"zap" as const},
];
const account=[
 {href:"/app/usage",label:"Использование",icon:"brain" as const},
 {href:"/app/wallet",label:"Баланс",icon:"wallet" as const},
 {href:"/app/account",label:"Аккаунт",icon:"user" as const},
 {href:"/app/settings",label:"Настройки",icon:"settings" as const},
 {href:"/app/help",label:"Помощь",icon:"search" as const},
];

export default function ClientAppChrome({children}:{children:ReactNode}){
 const pathname=usePathname();
 if(pathname==="/app")return <>{children}</>;
 const active=(href:string)=>href==="/app"?pathname===href:pathname===href||pathname.startsWith(`${href}/`);
 return <div className="clientChrome">
  <aside className="clientChromeNav" aria-label="Личный кабинет">
   <Link href="/app" className="clientChromeBrand"><span/>AI Workspace</Link>
   <nav className="clientChromePrimary">{primary.map(item=><Link key={item.href} href={item.href} className={active(item.href)?"active":""}><Icon name={item.icon} size={17}/><span>{item.label}</span></Link>)}</nav>
   <div className="clientChromeDivider"/>
   <nav className="clientChromeAccount">{account.map(item=><Link key={item.href} href={item.href} className={active(item.href)?"active":""}><Icon name={item.icon} size={17}/><span>{item.label}</span></Link>)}</nav>
  </aside>
  <div className="clientChromeMain">{children}</div>
 </div>;
}
