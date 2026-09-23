"use client";

import Link from "next/link";
import {usePathname} from "next/navigation";
import styles from "./admin.module.css";

const links=[
["Обзор","/admin-console"],
["AI-провайдеры","/admin-console/providers"],
["Интернет и live-данные","/admin-console/live-tools"],
["Экономика и закупки API","/admin-console/procurement"],
["Пользователи","/admin-console/users"],
["Финансы","/admin-console/finance"],
["Платежи","/admin-console/payments"],
["Состояние системы","/admin-console/system"],
["Безопасность","/admin-console/security"],
["Поддержка","/admin-console/support"],
] as const;

export default function AdminNav(){
 const pathname=usePathname();
 const active=(href:string)=>href==="/admin-console"?pathname===href:pathname.startsWith(href);
 return <aside className={styles.side}>
  <Link className={styles.brand} href="/admin-console">AI Workspace · Администратор</Link>
  <nav className={styles.nav} aria-label="Разделы панели администратора">
   {links.map(([label,href])=><Link className={active(href)?styles.active:""} aria-current={active(href)?"page":undefined} key={href} href={href}>{label}</Link>)}
  </nav>
  <div className={styles.bottom}><Link href="/app">← Рабочее пространство</Link></div>
 </aside>;
}
