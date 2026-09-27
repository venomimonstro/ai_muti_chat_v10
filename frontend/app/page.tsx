import type {Metadata} from "next";
import Link from "next/link";
import styles from "./landing.module.css";

export const metadata: Metadata = {
  title: "AI Workspace — рабочая AI-система для бизнеса и ежедневных задач",
  description: "Один рабочий интерфейс для текстов, документов, web-поиска, кода, изображений и проектов. System Lite, Pro и Max, прозрачный рублёвый баланс и API для бизнеса.",
  openGraph: {
    title: "AI Workspace — работайте, а не переключайте нейросети",
    description: "AI для документов, поиска, кода, изображений и проектов в одном рабочем пространстве.",
    type: "website",
    locale: "ru_RU",
  },
};

const capabilityCards = [
  {tag:"01", title:"Документы", text:"PDF, DOCX, XLSX, CSV и текстовые файлы остаются рядом с задачей и проектом.", tone:"violet"},
  {tag:"02", title:"Актуальный web-поиск", text:"Когда нужны свежие данные, система подключает web-поиск и возвращает результат с источниками.", tone:"blue"},
  {tag:"03", title:"Код и разработка", text:"Разбор ошибок, архитектура, ревью, прототипы и работа с GitHub без отдельного набора инструментов.", tone:"dark"},
  {tag:"04", title:"Изображения", text:"Генерация и анализ изображений в том же рабочем пространстве, где уже лежит контекст задачи.", tone:"peach"},
  {tag:"05", title:"Проекты и память", text:"Чаты, файлы и результаты объединяются вокруг проекта, чтобы не начинать работу заново.", tone:"mint"},
  {tag:"06", title:"API для бизнеса", text:"Ключи, бюджеты, rate limits и контроль расходов для подключения AI к вашим продуктам.", tone:"sand"},
];

const levels = [
  {name:"System Lite", label:"Быстро и экономно", text:"Для коротких текстов, идей, резюме и простых ежедневных задач.", items:["короткие тексты и идеи","переформулировки и резюме","простые вопросы"], badge:"БЫСТРО"},
  {name:"System Pro", label:"Основной рабочий режим", text:"Баланс качества, скорости и стоимости для большинства рабочих задач.", items:["маркетинг и исследования","документы и аналитика","код и рабочие проекты"], badge:"РЕКОМЕНДУЕМ"},
  {name:"System Max", label:"Максимальная глубина", text:"Для сложной разработки, многоэтапного анализа и задач с высокой ценой ошибки.", items:["сложная разработка","глубокий анализ","критичные решения"], badge:"МАКСИМУМ"},
];

const useCases = [
  ["Маркетинг", "Исследования, реклама, контент, гипотезы и конкурентный анализ."],
  ["Разработка", "Архитектура, отладка, ревью, документация и помощь с кодом."],
  ["Документы", "Договоры, таблицы, отчёты, инструкции и большие массивы текста."],
  ["Исследования", "Сбор данных, web-поиск, сравнение вариантов и выводы."],
  ["Операционная работа", "Письма, планы, расчёты, встречи и подготовка материалов."],
  ["Команды и продукты", "Проекты, API, единый баланс и повторяемые AI-сценарии."],
];

const faq = [
  ["Нужно ли разбираться в моделях и провайдерах?", "Нет. Для обычной работы достаточно выбрать System Lite, Pro или Max. Техническая маршрутизация остаётся внутри сервиса."],
  ["Чем отличаются Lite, Pro и Max?", "Lite — для простых и быстрых задач, Pro — универсальный режим по умолчанию, Max — для сложного анализа, разработки и задач, где качество важнее скорости."],
  ["Есть обязательная подписка?", "Нет. Баланс пополняется вручную. Перед платным запросом система резервирует безопасный максимум, после ответа списывает фактическую стоимость и возвращает остаток резерва."],
  ["Можно работать с файлами и проектами?", "Да. Чаты и материалы можно объединять в проекты, загружать документы и использовать их как контекст для дальнейшей работы."],
  ["Что произойдёт при сбое внешнего AI-сервиса?", "Система контролирует состояние подключений. Незавершённый запрос не должен превращаться в успешную платную операцию."],
];

const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: faq.map(([name, text]) => ({
    "@type": "Question",
    name,
    acceptedAnswer: {"@type": "Answer", text},
  })),
};

export default function LandingPage() {
  return <main className={styles.page}>
    <script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(faqSchema)}} />

    <div className={styles.announcement}>
      <span>AI Workspace</span>
      <p>Рабочая AI-система без обязательной подписки</p>
      <Link href="/pricing">Посмотреть стоимость →</Link>
    </div>

    <div className={styles.headerWrap}>
      <header className={styles.header}>
        <Link className={styles.brand} href="/" aria-label="AI Workspace — главная"><span>✦</span><b>AI Workspace</b></Link>
        <nav className={styles.nav} aria-label="Основная навигация">
          <a href="#why">Почему</a>
          <a href="#levels">Уровни</a>
          <a href="#product">Возможности</a>
          <Link href="/pricing">Стоимость</Link>
          <a href="#faq">FAQ</a>
        </nav>
        <div className={styles.headerActions}><Link className={styles.login} href="/login">Войти</Link><Link className={styles.headerCta} href="/register">Начать бесплатно</Link></div>
      </header>
    </div>

    <section className={styles.hero}>
      <div className={styles.heroGlowOne} aria-hidden="true" />
      <div className={styles.heroGlowTwo} aria-hidden="true" />
      <div className={styles.heroCopy}>
        <div className={styles.heroBadge}><span>●</span> AI ДЛЯ РЕАЛЬНОЙ РАБОТЫ</div>
        <h1>Работайте,<br/><em>а не переключайте</em><br/>нейросети.</h1>
        <p>Тексты, документы, актуальный web-поиск, код, изображения и проекты — в одном интерфейсе. Выбираете только <strong>Lite, Pro или Max</strong>. Остальное система берёт на себя.</p>
        <div className={styles.heroActions}>
          <Link className={styles.heroPrimary} href="/register">Начать работу <span>↗</span></Link>
          <a className={styles.heroSecondary} href="#product">Посмотреть возможности</a>
        </div>
        <div className={styles.heroTrust}><span>Без обязательной подписки</span><span>Оплата в рублях</span><span>Контроль расходов</span></div>
      </div>

      <div className={styles.heroProduct} aria-label="Пример рабочего пространства AI Workspace">
        <div className={styles.browserTop}><div><i/><i/><i/></div><span>workspace.ai</span><b>•••</b></div>
        <div className={styles.productBody}>
          <aside className={styles.productSidebar}>
            <div className={styles.miniBrand}><span>✦</span><b>Workspace</b></div>
            <button type="button">＋ Новый чат</button>
            <small>ПРОЕКТЫ</small>
            <p>▣ Запуск продукта</p><p>▣ Маркетинг</p><p>▣ Исследование рынка</p>
            <small>ИСТОРИЯ</small>
            <p className={styles.activeRow}>Анализ стратегии</p><p>План продвижения</p>
          </aside>
          <div className={styles.productChat}>
            <div className={styles.chatTop}><div><b>Анализ стратегии</b><span>Проект · Запуск продукта</span></div><em>System Pro</em></div>
            <div className={styles.chatMessages}>
              <div className={styles.userBubble}>Разбери мой план запуска. Найди главные риски и предложи, что исправить до старта.</div>
              <div className={styles.aiAnswer}>
                <div className={styles.aiHead}><span>✦</span><div><b>AI Workspace</b><small>System Pro · анализ завершён</small></div></div>
                <p>До запуска я бы закрыл три риска:</p>
                <div className={styles.risk}><b>01</b><span><strong>Слишком широкий MVP</strong>Сократите первый релиз до одного главного сценария и измеримой пользы.</span></div>
                <div className={styles.risk}><b>02</b><span><strong>Нет критерия спроса</strong>Зафиксируйте метрику, после которой продукт можно масштабировать.</span></div>
                <div className={styles.risk}><b>03</b><span><strong>Экономика проверяется поздно</strong>Посчитайте стоимость активного пользователя до масштабирования трафика.</span></div>
              </div>
            </div>
            <div className={styles.composerMock}><span>＋</span><p>Продолжить диалог…</p><button type="button">System Pro⌄</button><b>↑</b></div>
          </div>
        </div>
        <div className={styles.costChip}><small>ПОСЛЕ ЗАПРОСА</small><b>Списано по факту</b><span>остаток резерва возвращён</span></div>
      </div>
    </section>

    <section className={styles.signalBar} aria-label="Ключевые свойства продукта">
      <div><b>01</b><span><strong>Один интерфейс</strong>для всех AI-задач</span></div>
      <div><b>02</b><span><strong>3 уровня</strong>вместо списка моделей</span></div>
      <div><b>03</b><span><strong>₽ баланс</strong>без обязательной подписки</span></div>
      <div><b>04</b><span><strong>Контроль</strong>расходов и операций</span></div>
    </section>

    <section className={styles.whySection} id="why">
      <div className={styles.sectionLabel}>ПОЧЕМУ ЭТО ПРОЩЕ</div>
      <div className={styles.whyHeader}><h2>AI должен убирать работу.<br/>Не добавлять новую.</h2><p>Вместо десятка сервисов, подписок и технических названий — одно рабочее пространство и понятный выбор мощности.</p></div>
      <div className={styles.comparison}>
        <article className={styles.oldWay}><span>ОБЫЧНО</span><h3>Вы управляете нейросетями</h3><ul><li>какую модель выбрать?</li><li>где остался нужный контекст?</li><li>какая подписка ещё активна?</li><li>где искать файлы и историю?</li></ul></article>
        <div className={styles.switchArrow}>→</div>
        <article className={styles.newWay}><span>AI WORKSPACE</span><h3>Нейросеть работает на вас</h3><ul><li>опишите задачу обычным языком</li><li>выберите Lite, Pro или Max</li><li>получите результат в проекте</li><li>продолжайте с тем же контекстом</li></ul></article>
      </div>
    </section>

    <section className={styles.levelSection} id="levels">
      <div className={styles.sectionLabel}>SYSTEM LEVELS</div>
      <div className={styles.sectionHeader}><h2>Три режима вместо каталога моделей</h2><p>Выбирайте по сложности задачи, а не по техническим характеристикам провайдера.</p></div>
      <div className={styles.levelGrid}>{levels.map((level,index)=><article key={level.name} className={`${styles.levelCard} ${index===1?styles.levelPro:""}`}>
        <div className={styles.levelMeta}><span>{level.badge}</span><b>0{index+1}</b></div>
        <h3>{level.name}</h3><h4>{level.label}</h4><p>{level.text}</p>
        <ul>{level.items.map(item=><li key={item}>{item}</li>)}</ul>
        {index===1?<Link href="/register">Попробовать System Pro <span>→</span></Link>:<span className={styles.levelFoot}>Доступен в рабочем пространстве</span>}
      </article>)}</div>
    </section>

    <section className={styles.productSection} id="product">
      <div className={styles.sectionLabel}>ВОЗМОЖНОСТИ</div>
      <div className={styles.sectionHeader}><h2>Не просто чат.<br/>Рабочая AI-система.</h2><p>Инструменты остаются в одном контексте и помогают доводить задачу до результата.</p></div>
      <div className={styles.bento}>{capabilityCards.map((item,index)=><article key={item.title} className={`${styles.bentoCard} ${styles[item.tone]} ${index===2?styles.bentoWide:""}`}>
        <span>{item.tag}</span><h3>{item.title}</h3><p>{item.text}</p><i>↗</i>
      </article>)}</div>
    </section>

    <section className={styles.workflowSection}>
      <div className={styles.workflowCopy}><div className={styles.sectionLabel}>КАК ЭТО РАБОТАЕТ</div><h2>От задачи до результата — четыре простых шага</h2><p>Никакой настройки провайдеров перед первым запросом.</p><Link href="/getting-started">Посмотреть начало работы →</Link></div>
      <div className={styles.workflowSteps}><article><b>01</b><div><h3>Опишите задачу</h3><p>Напишите запрос, прикрепите файл или продолжите работу внутри проекта.</p></div></article><article><b>02</b><div><h3>Выберите уровень</h3><p>Lite для простого, Pro для большинства задач, Max для самого сложного.</p></div></article><article><b>03</b><div><h3>Система выполняет работу</h3><p>Маршрутизация, поиск, контекст и расчёт стоимости происходят внутри.</p></div></article><article><b>04</b><div><h3>Продолжайте с контекстом</h3><p>История, файлы и результаты остаются рядом с проектом.</p></div></article></div>
    </section>

    <section className={styles.useSection}>
      <div className={styles.sectionLabel}>СЦЕНАРИИ</div>
      <div className={styles.sectionHeader}><h2>Один продукт для разных типов работы</h2><p>От разового вопроса до ежедневной рабочей среды команды.</p></div>
      <div className={styles.useGrid}>{useCases.map(([title,text],index)=><article key={title}><span>0{index+1}</span><div><h3>{title}</h3><p>{text}</p></div></article>)}</div>
    </section>

    <section className={styles.economicsSection}>
      <div className={styles.economicsCopy}><span>ПРОЗРАЧНАЯ ЭКОНОМИКА</span><h2>Платите за работу,<br/>а не за набор подписок.</h2><p>Пополняйте единый рублёвый баланс. Система резервирует безопасный максимум, после ответа списывает фактическую стоимость и возвращает неиспользованный остаток.</p><Link href="/pricing">Как считается стоимость <b>→</b></Link></div>
      <div className={styles.moneyPanel}><div><small>ДО ЗАПРОСА</small><b>Резерв</b><span>Баланс не уходит в минус</span></div><div><small>ПОСЛЕ ОТВЕТА</small><b>Списание по факту</b><span>Видно в истории операций</span></div><div><small>ПРИ СБОЕ</small><b>Защита остатка</b><span>Незавершённая операция не считается успешной</span></div></div>
    </section>

    <section className={styles.apiSection}>
      <div className={styles.apiCard}><div className={styles.apiTop}><span>API</span><b>OpenAI-compatible</b></div><pre>{`POST /v1/chat/completions\nAuthorization: Bearer ••••••••\n\n{\n  "messages": [\n    {"role": "user", "content": "..."}\n  ]\n}`}</pre><div className={styles.apiStatus}><i/> API ready</div></div>
      <div className={styles.apiCopy}><div className={styles.sectionLabel}>ДЛЯ БИЗНЕСА</div><h2>Встройте тот же AI в свой продукт</h2><p>API-ключи, бюджеты, rate limits и контроль расходов — без создания отдельной AI-инфраструктуры для каждого сценария.</p><ul><li>единый контроль доступа</li><li>лимиты и бюджеты</li><li>история расходов</li><li>совместимый API</li></ul><Link className={styles.darkCta} href="/register">Создать аккаунт <span>→</span></Link></div>
    </section>

    <section className={styles.faqSection} id="faq">
      <div className={styles.sectionLabel}>FAQ</div>
      <div className={styles.sectionHeader}><h2>До регистрации должно быть всё понятно</h2><p>Основные вопросы о режимах, оплате и рабочем процессе.</p></div>
      <div className={styles.faqList}>{faq.map(([question,answer])=><details key={question}><summary><span>{question}</span><b>＋</b></summary><p>{answer}</p></details>)}</div>
      <Link className={styles.faqMore} href="/faq">Все вопросы и ответы →</Link>
    </section>

    <section className={styles.finalCta}>
      <div className={styles.finalOrb} aria-hidden="true" />
      <span>НАЧНИТЕ С РЕАЛЬНОЙ ЗАДАЧИ</span><h2>Откройте чат.<br/>Выберите System Pro.<br/>И просто работайте.</h2><p>Без изучения моделей, сложных настроек и обязательной ежемесячной подписки.</p>
      <div><Link className={styles.finalPrimary} href="/register">Создать аккаунт <b>↗</b></Link><Link className={styles.finalSecondary} href="/login">Уже есть аккаунт</Link></div><small>AI Workspace · продукт BBTEC</small>
    </section>

    <footer className={styles.footer}>
      <div className={styles.footerBrand}><Link className={styles.brand} href="/"><span>✦</span><b>AI Workspace</b></Link><p>AI для работы, документов, поиска, кода и проектов — в одном пространстве.</p><small>Продукт BBTEC</small></div>
      <div><b>Продукт</b><a href="#levels">System Lite / Pro / Max</a><Link href="/pricing">Стоимость</Link><Link href="/getting-started">Начало работы</Link><Link href="/status">Статус</Link></div>
      <div><b>Помощь</b><Link href="/faq">FAQ</Link><Link href="/app/help">Поддержка</Link><Link href="/forgot-password">Восстановить доступ</Link></div>
      <div><b>Документы</b><Link href="/legal/offer">Оферта</Link><Link href="/legal/privacy">Конфиденциальность</Link><Link href="/legal/refunds">Возвраты</Link><Link href="/legal/acceptable-use">Правила использования</Link></div>
      <div className={styles.footerBottom}><span>© {new Date().getFullYear()} AI Workspace · BBTEC</span><span>Системные уровни скрывают внутреннюю техническую маршрутизацию и могут использовать разные подключённые AI-модели.</span></div>
    </footer>
  </main>;
}
