import Link from "next/link";
import styles from "./admin.module.css";

const links=[
["Обзор","/admin-console"],["Аналитика","/admin-console/analytics"],["Пользователи","/admin-console/users"],["Провайдеры и цены","/admin-console/providers"],["Финансы","/admin-console/finance"],["Платежи","/admin-console/payments"],["Безопасность","/admin-console/security"],["Поддержка","/admin-console/support"],["Операции","/admin-console/operations"],["Launch drills","/admin-console/drills"],["Compliance","/admin-console/compliance"]
];
export default function AdminNav(){return <aside className={styles.side}><Link className={styles.brand} href="/admin-console">AI Workspace · Admin</Link><nav className={styles.nav}>{links.map(([label,href])=><Link key={href} href={href}>{label}</Link>)}</nav><div className={styles.bottom}><Link href="/app">← Workspace</Link><Link href="/security/mfa">MFA / безопасность</Link></div></aside>}
