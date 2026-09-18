import Link from "next/link";
import styles from "../commercial.module.css";

const items=[
["Что такое AUTO Router?","Это слой маршрутизации, который оценивает тип задачи, качество доступных моделей, стоимость, задержку, состояние провайдера и нужные capabilities. В режиме вручную модель выбираете вы."],
["Почему цена может отличаться между запросами?","Модели тарифицируют входные и выходные токены по-разному. Сервис резервирует верхнюю оценку до запуска, а окончательное списание рассчитывает по фактическому usage и pricing snapshot запроса."],
["Что происходит, если провайдер упал?","До первого токена сервис может повторить запрос или переключиться на разрешённый fallback. Если операция не завершена, резерв освобождается и незавершённый запрос не должен превращаться в двойное списание."],
["Можно ли работать с PDF и файлами?","Да. Текстовые PDF обрабатываются постранично с provenance. Для сканов требуется OCR fallback. DOCX, XLSX, CSV, TXT/MD также проходят безопасный файловый pipeline."],
["Как защищены ключи и аккаунт?","Ключи AI-провайдеров остаются на сервере, B2B API-ключи хэшируются с отдельным pepper, администраторский контур поддерживает TOTP MFA и recovery codes."],
["Использует ли сервис мои файлы как инструкции?","Нет. Содержимое файлов и веб-источников помечается как недоверенный контекст; найденные внутри команды не должны менять системные правила или автоматически запускать инструменты."],
["Можно ли использовать API?","Да. B2B API имеет отдельные ключи, scopes, model/endpoint allowlists, бюджеты, RPM/concurrency limits и IP allowlist."],
];
export default function FAQPage(){return <main className={styles.page}><div className={styles.shell}><header className={styles.top}><Link className={styles.brand} href="/">AI Workspace</Link><nav className={styles.nav}><Link href="/app">Открыть приложение</Link><Link href="/pricing">Стоимость</Link><Link href="/getting-started">Начало работы</Link></nav></header><section className={styles.hero}><h1>Частые вопросы</h1><p>Коротко о моделях, оплате, файлах, стабильности и безопасности.</p></section><section className={styles.faq}>{items.map(([q,a])=><details key={q}><summary>{q}</summary><p>{a}</p></details>)}</section><div className={styles.actions}><Link className={styles.cta} href="/app">Начать работу</Link><Link className={styles.secondary} href="/">На главную</Link></div></div></main>}
