"use client";

import Link from "next/link";
import {usePathname} from "next/navigation";
import styles from "../admin.module.css";

const tabs = [
  ["Экономика", "/admin-console/procurement"],
  ["API-ключи", "/admin-console/procurement/keys"],
  ["Закупочные ордера", "/admin-console/procurement/orders"],
] as const;

export default function ProcurementLayout({children}:{children:React.ReactNode}) {
  const pathname = usePathname();
  return <>
    <div className={styles.section} style={{padding:"10px 14px",marginBottom:16}}>
      <div className={styles.actions} style={{gap:8,flexWrap:"wrap"}}>
        {tabs.map(([label,href])=>{
          const active = href==="/admin-console/procurement" ? pathname===href : pathname.startsWith(href);
          return <Link key={href} href={href} className={`${styles.button} ${active?styles.primary:""}`}>{label}</Link>;
        })}
      </div>
    </div>
    {children}
  </>;
}
