import type {Metadata} from "next";
import Link from "next/link";
import styles from "./landing.module.css";

export const metadata: Metadata = {
  title: "AI Workspace — ведущие AI-модели в одном сервисе",
  description: "GPT, Claude, Gemini, Grok, DeepSeek, документы, изображения, web-поиск и API в одном рабочем пространстве с единым рублёвым балансом и AUTO Router.",
  openGraph: {
    title: "AI Workspace — один сервис для разных AI-задач",
    description: "AUTO Router выбирает подходящую подключённую модель под задачу. Чаты, проекты, документы, изображения, web-поиск и API в одном интерфейсе.",
    type: "website",
    locale: "ru_RU",
  },
};

const models = ["GPT", "Claude", "Gemini", "Grok", "DeepSeek"];
const capabilities = [
  ["AUTO Router", "Опишите задачу — сервис учитывает требования, качество, стоимость, задержку и состояние провайдера при выборе модели."],
  ["Документы и проекты", "Храните контекст в проектах, загружайте PDF, DOCX, XLSX, CSV и находите ответы с привязкой к источникам."],
  ["Изображения и vision", "Создавайте изображения и передавайте изображения поддерживаемым моделям для анализа."],
  ["Актуальная информация", "Для задач, которым нужны свежие данные, сервис может подключать web-поиск и возвращать источники."],
  ["Сравнение моделей", "Отправьте один запрос нескольким моделям и сравните ответы, стоимость и результат в одном окне."],
  ["API для бизнеса", "OpenAI-compatible API, ключи, лимиты, бюджеты, allowlist моделей и единый рублёвый биллинг."],
];

export default function LandingPage() {
  return <main className={styles.page}>
    <header className={styles.header}>
      <Link className={styles.brand} href="/" aria-label="AI Workspace — главная"><span>✦</span>AI Workspace</Link>
      <nav className={styles.nav} aria-label="Основная навигация">
        <a href="#product">Возможности</a>
        <a href="#auto">AUTO Router</a>
        <Link href="/pricing">Стоимость</Link>
        <Link href="/faq">FAQ</Link>
      </nav>
      <div className={styles.headerActions}><Link className={styles.login} href="/app">Войти</Link><Link className={styles.primarySmall} href="/app">Начать работу</Link></div>
    </header>

    <section className={styles.hero}>
      <div className={styles.heroCopy}>
        <div className={styles.kicker}>ОДНО AI-ПРОСТРАНСТВО ВМЕСТО НЕСКОЛЬКИХ СЕРВИСОВ</div>
        <h1>Не выбирайте нейросеть вручную.<br/><span>AUTO подберёт модель под задачу.</span></h1>
        <p>Чаты, ведущие AI-модели, проекты, документы, изображения, web-поиск и API — в одном интерфейсе с единым рублёвым балансом.</p>
        <div className={styles.heroActions}><Link className={styles.primary} href="/app">Попробовать AI Workspace</Link><a className={styles.secondary} href="#auto">Как работает AUTO</a></div>
        <div className={styles.trustLine}><span>Прозрачная стоимость каждого запроса</span><span>Резерв до запроса, списание по факту</span><span>Fallback при сбоях провайдера</span></div>
      </div>
      <div className={styles.productPreview} aria-label="Пример интерфейса AI Workspace">
        <div className={styles.previewTop}><span className={styles.logoMark}>✦</span><b>Новый чат</b><span className={styles.autoBadge}>AUTO · Баланс</span></div>
        <div className={styles.previewBody}>
          <div className={styles.userBubble}>Сравни три варианта запуска продукта и выдели основные риски.</div>
          <div className={styles.aiCard}><div><span className={styles.aiIcon}>✦</span><b>AI Workspace</b><em>выбрана подходящая модель</em></div><p><strong>1. Быстрый запуск.</strong> Минимальный набор функций, проверка спроса и экономики до масштабирования.</p><p><strong>2. Вертикальный продукт.</strong> Фокус на одном сегменте и глубоком сценарии использования.</p><div className={styles.previewMeta}><span>AUTO Router</span><span>источники</span><span>стоимость зафиксирована</span></div></div>
        </div>
      </div>
    </section>

    <section className={styles.modelStrip} aria-label="Поддерживаемые семейства моделей"><span>Подключайте модели разных провайдеров:</span>{models.map((model) => <b key={model}>{model}</b>)}</section>

    <section className={styles.section} id="auto">
      <div className={styles.sectionIntro}><span>AUTO ROUTER</span><h2>Один запрос — система сама решает, какой AI использовать</h2><p>Вы можете выбрать модель вручную или отдать выбор AUTO Router. Маршрут учитывает возможности модели, контекст задачи, цену, задержку и здоровье провайдера.</p></div>
      <div className={styles.autoGrid}>
        <article><small>01</small><h3>Эконом</h3><p>Приоритет стоимости при заданном минимуме качества.</p></article>
        <article className={styles.featured}><small>02</small><h3>Баланс</h3><p>Режим по умолчанию: качество, цена и скорость учитываются вместе.</p></article>
        <article><small>03</small><h3>Максимум</h3><p>Приоритет качества для сложного анализа и разработки.</p></article>
      </div>
    </section>

    <section className={styles.section} id="product">
      <div className={styles.sectionIntro}><span>РАБОЧЕЕ ПРОСТРАНСТВО</span><h2>Не просто чат с моделью</h2><p>Контекст, история, файлы, память, стоимость и результаты остаются в одном рабочем пространстве.</p></div>
      <div className={styles.capabilityGrid}>{capabilities.map(([title, text], index) => <article key={title}><div className={styles.capIcon}>{String(index + 1).padStart(2, "0")}</div><h3>{title}</h3><p>{text}</p></article>)}</div>
    </section>

    <section className={styles.economics}>
      <div><span>ЕДИНЫЙ БАЛАНС</span><h2>Платите за фактическое использование, а не за набор отдельных подписок</h2><p>Перед запросом сервис рассчитывает верхний предел и резервирует средства. После ответа стоимость пересчитывается по фактическому usage и зафиксированному pricing snapshot.</p><Link className={styles.secondaryLight} href="/pricing">Подробнее о стоимости →</Link></div>
      <div className={styles.economicsCard}><div><small>До запроса</small><b>Резерв</b><p>Защита от ухода баланса в минус.</p></div><div><small>После ответа</small><b>По факту</b><p>Списание по фактическому usage.</p></div><div><small>При ошибке</small><b>Освобождение</b><p>Незавершённый запрос не превращается в списание.</p></div></div>
    </section>

    <section className={styles.apiSection}>
      <div className={styles.codeCard}><div><span></span><span></span><span></span></div><pre>{`POST /v1/chat/completions\nAuthorization: Bearer aw_live_...\n\n{\n  "model": "your-model",\n  "messages": [...]\n}`}</pre></div>
      <div><span className={styles.label}>ДЛЯ КОМАНД И БИЗНЕСА</span><h2>Один API для подключённых AI-моделей</h2><p>OpenAI-compatible endpoint, API-ключи, scopes, ограничения моделей и endpoints, месячные бюджеты, rate limits, concurrency и IP allowlist.</p><Link className={styles.primary} href="/app">Открыть рабочее пространство</Link></div>
    </section>

    <section className={styles.finalCta}><span>ГОТОВЫ НАЧАТЬ?</span><h2>Один интерфейс. Один баланс. Модели под разные задачи.</h2><p>Создайте аккаунт, подтвердите email и начните с AUTO Router или выберите модель вручную.</p><div><Link className={styles.primary} href="/app">Начать работу</Link><Link className={styles.secondary} href="/faq">Посмотреть FAQ</Link></div></section>

    <footer className={styles.footer}>
      <div><Link className={styles.brand} href="/"><span>✦</span>AI Workspace</Link><p>Универсальное рабочее пространство для AI-задач.</p></div>
      <div><b>Продукт</b><Link href="/pricing">Стоимость</Link><Link href="/faq">FAQ</Link><Link href="/getting-started">Начало работы</Link><Link href="/status">Статус</Link></div>
      <div><b>Документы</b><a href="/legal/offer">Оферта</a><a href="/legal/privacy">Конфиденциальность</a><a href="/legal/refunds">Возвраты</a><a href="/legal/acceptable-use">Правила использования</a></div>
      <div><b>Аккаунт</b><Link href="/app">Войти / зарегистрироваться</Link><Link href="/forgot-password">Восстановить доступ</Link></div>
      <small className={styles.disclaimer}>Названия сторонних AI-продуктов и компаний используются только для идентификации совместимых провайдеров и принадлежат их правообладателям.</small>
    </footer>
  </main>;
}
