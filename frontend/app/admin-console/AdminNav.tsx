import Link from "next/link";
import styles from "./admin.module.css";

const links=[
["Обзор","/admin-console"],
["Состояние системы","/admin-console/system"],
["Аналитика","/admin-console/analytics"],
["Пользователи","/admin-console/users"],
["Провайдеры и цены","/admin-console/providers"],
["Финансы","/admin-console/finance"],
["Платежи","/admin-console/payments"],
["Безопасность","/admin-console/security"],
["Поддержка","/admin-console/support"],
["Операции и релизы","/admin-console/operations"],
["Проверки перед запуском","/admin-console/drills"],
["Юридическая готовность","/admin-console/compliance"]
];
export default function AdminNav(){return <aside className={styles.side}><Link className={styles.brand} href="/admin-console">AI Workspace · Администратор</Link><nav className={styles.nav}>{links.map(([label,href])=><Link key={href} href={href}>{label}</Link>)}</nav><div className={styles.bottom}><Link href="/app">← Рабочее пространство</Link><Link href="/security/mfa">Двухфакторная защита</Link></div></aside>}
