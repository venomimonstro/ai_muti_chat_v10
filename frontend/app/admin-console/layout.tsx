import type {Metadata} from "next";
import AdminNav from "./AdminNav";
import styles from "./admin.module.css";

export const metadata:Metadata={title:"AI Workspace — Панель администратора",robots:{index:false,follow:false}};
export default function AdminLayout({children}:{children:React.ReactNode}){return <div className={styles.shell}><AdminNav/><main className={styles.main}>{children}</main></div>}
