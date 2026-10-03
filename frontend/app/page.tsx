import type {Metadata} from "next";
import Link from "next/link";
import styles from "./landing.module.css";

export const metadata: Metadata = {
  title: "AIlegend — AI-чат, агенты, разработка и API в одном сервисе",
  description: "AIlegend объединяет AI-чат, выбор уровня или конкретной нейросети, web-поиск, файлы, проекты, агентов, Dev Studio, изображения и API с единым рублёвым балансом.",
  openGraph: {
    title: "AIlegend — одна AI-платформа вместо набора разрозненных сервисов",
    description: "Работайте с нейросетями, файлами и проектами, запускайте агентов и разработку в одном сервисе.",
    type: "website",
    locale: "ru_RU",
  },
};

const capabilityCards = [
  {tag:"01", title:"AI Chat + проекты", text:"Чаты, документы, файлы и история работы остаются внутри проекта — без постоянной пересборки контекста.", tone:"violet"},
  {tag:"02", title:"Agent Studio", text:"Собирайте повторяемые AI-процессы, подключайте инструменты, web-действия, расписания и интеграции.", tone:"mint"},
  {tag:"03", title:"Dev Studio", text:"Планирование, код, проверка изменений и GitHub-сценарии в отдельной среде для сложной разработки.", tone:"dark"},
  {tag:"04", title:"Актуальный web-поиск", text:"Для задач со свежими данными система подключает поиск и возвращает результат вместе с источниками.", tone:"blue"},
  {tag:"05", title:"Image Studio + Compare", text:"Генерируйте изображения и сравнивайте подходы нескольких AI-моделей без ухода из рабочего пространства.", tone:"peach"},
  {tag:"06", title:"API для бизнеса", text:"Подключайте AI к своим продуктам: ключи, бюджеты, rate limits, история расходов и единый контроль.", tone:"sand"},
];

const levels = [
  {name:"System Lite", label:"Быстро и экономно", text:"Для коротких текстов, идей, резюме и простых ежедневных задач.", items:["короткие тексты и идеи","переформулировки и резюме","простые вопросы"], badge:"БЫСТРО"},
  {name:"System Pro", label:"Основной рабочий режим", text:"Баланс качества, скорости и стоимости для большинства рабочих задач.", items:["маркетинг и исследования","документы и аналитика","код и рабочие проекты"], badge:"РЕКОМЕНДУЕМ"},
  {name:"System Max", label:"Максимальная глубина", text:"Для сложной разработки, многоэтапного анализа и задач с высокой ценой ошибки.", items:["сложная разработка","глубокий анализ","критичные решения"], badge:"МАКСИМУМ"},
];

const useCases = [
  ["Маркетинг", "Исследования, реклама, контент, гипотезы, web-поиск и конкурентный анализ."],
  ["Разработка", "Архитектура, отладка, ревью, GitHub и многошаговая работа в Dev Studio."],
  ["Бизнес-процессы", "Повторяемые задачи, AI-агенты, расписания, интеграции и контроль запусков."],
  ["Документы и аналитика", "PDF, таблицы, отчёты, инструкции, исследования и проектный контекст."],
  ["Креатив и изображения", "Image Studio, идеи, визуальные концепции и работа с изображениями."],
  ["Команды и продукты", "Проекты, API, лимиты, бюджеты и единый контроль расходов."],
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
      <span>AIlegend</span>
      <p>AI-платформа для работы без обязательной подписки</p>
      <Link href="/pricing">Посмотреть стоимость →</Link>
    </div>

    <div className={styles.headerWrap}>
      <header className={styles.header}>
        <Link className={styles.brand} href="/" aria-label="AIlegend — главная"><span>✦</span><b>AIlegend</b></Link>
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
        <div className={styles.heroBadge}><span>●</span> НЕ ПРОСТО АГРЕГАТОР НЕЙРОСЕТЕЙ</div>
        <h1>Один кабинет для AI.<br/><em>От простого вопроса</em><br/>до работающего процесса.</h1>
        <p><strong>Выберите уровень сложности или конкретную нейросеть прямо в чате.</strong> Работайте с файлами и проектами, запускайте AI-агентов, Dev Studio, изображения и API — с единым рублёвым балансом.</p>
        <div className={styles.heroActions}>
          <Link className={styles.heroPrimary} href="/register">Начать работу бесплатно <span>↗</span></Link>
          <a className={styles.heroSecondary} href="#product">Посмотреть продукт</a>
        </div>
        <div className={styles.heroTrust}><span>Без обязательной подписки</span><span>Уровень или конкретная модель в один клик</span><span>Расходы в рублях, а не токенах</span></div>
      </div>

      <div className={styles.heroProduct} aria-label="Пример рабочего пространства AIlegend">
        <div className={styles.browserTop}><div><i/><i/><i/></div><span>ailegend.ru</span><b>•••</b></div>
        <div className={styles.productBody}>
          <aside className={styles.productSidebar}>
            <div className={styles.miniBrand}><span>✦</span><b>AIlegend</b></div>
            <button type="button">＋ Новый чат</button>
            <small>РАБОЧЕЕ ПРОСТРАНСТВО</small>
            <p className={styles.activeRow}>✦ AI Chat</p><p>▣ Проекты</p><p>◇ Agent Studio</p><p>⌘ Dev Studio</p>
            <small>НЕДАВНЕЕ</small>
            <p>Запуск продукта</p><p>SEO стратегия</p>
          </aside>
          <div className={styles.productChat}>
            <div className={styles.chatTop}><div><b>Анализ стратегии</b><span>Проект · Запуск продукта</span></div><em>System Pro</em></div>
            <div className={styles.chatMessages}>
              <div className={styles.userBubble}>Разбери мой план запуска. Найди главные риски и предложи, что исправить до старта.</div>
              <div className={styles.aiAnswer}>
                <div className={styles.aiHead}><span>✦</span><div><b>AIlegend</b><small>System Pro · анализ завершён</small></div></div>
                <p>До запуска я бы закрыл три риска:</p>
                <div className={styles.risk}><b>01</b><span><strong>Слишком широкий MVP</strong>Сократите первый релиз до одного главного сценария и измеримой пользы.</span></div>
                <div className={styles.risk}><b>02</b><span><strong>Нет критерия спроса</strong>Зафиксируйте метрику, после которой продукт можно масштабировать.</span></div>
                <div className={styles.risk}><b>03</b><span><strong>Экономика проверяется поздно</strong>Посчитайте стоимость активного пользователя до масштабирования трафика.</span></div>
              </div>
            </div>
            <div className={styles.composerMock}><span>＋</span><p>Напишите сообщение…</p><button type="button">Pro · выбрать⌄</button><b>↑</b></div>
          </div>
        </div>
        <div className={styles.costChip}><small>ПОСЛЕ ЗАПРОСА</small><b>Списано по факту</b><span>остаток резерва возвращён</span></div>
      </div>
    </section>

    <section className={styles.signalBar} aria-label="Ключевые свойства продукта">
      <div><b>01</b><span><strong>Один AI-чат</strong>с выбором уровня или модели</span></div>
      <div><b>02</b><span><strong>AI-агенты</strong>для повторяемой работы</span></div>
      <div><b>03</b><span><strong>₽ баланс</strong>без обязательной подписки</span></div>
      <div><b>04</b><span><strong>Единый кабинет</strong>чат, проекты, dev и API</span></div>
    </section>

    <section className={styles.whySection} id="why">
      <div className={styles.sectionLabel}>ПОЧЕМУ AILEGEND</div>
      <div className={styles.whyHeader}><h2>Не выбирайте между десятью сервисами.<br/>Выбирайте, что нужно сделать.</h2><p>В AIlegend можно начать с обычного чата, выбрать уровень или конкретную нейросеть, добавить файлы и проект, а повторяемую работу перенести в Agent Studio или Dev Studio.</p></div>
      <div className={styles.comparison}>
        <article className={styles.oldWay}><span>ОБЫЧНО</span><h3>Работа разбросана по сервисам</h3><ul><li>отдельный чат и отдельный поиск</li><li>файлы и история живут в разных местах</li><li>для автоматизации нужен ещё один сервис</li><li>для разработки — ещё один AI-инструмент</li></ul></article>
        <div className={styles.switchArrow}>→</div>
        <article className={styles.newWay}><span>AILEGEND</span><h3>Одна среда вокруг вашей работы</h3><ul><li>чат и web-поиск в одном контексте</li><li>проекты, документы и история рядом</li><li>Agent Studio для повторяемых процессов</li><li>Dev Studio и API для следующего уровня</li></ul></article>
      </div>
    </section>

    <section className={styles.levelSection} id="levels">
      <div className={styles.sectionLabel}>SYSTEM LEVELS</div>
      <div className={styles.sectionHeader}><h2>Выберите сложность — или конкретную нейросеть</h2><p>Автоматический режим подбирает подходящий уровень сам. Если хотите полный контроль, конкретная доступная модель выбирается прямо в форме отправки сообщения.</p></div>
      <div className={styles.levelGrid}>{levels.map((level,index)=><article key={level.name} className={`${styles.levelCard} ${index===1?styles.levelPro:""}`}>
        <div className={styles.levelMeta}><span>{level.badge}</span><b>0{index+1}</b></div>
        <h3>{level.name}</h3><h4>{level.label}</h4><p>{level.text}</p>
        <ul>{level.items.map(item=><li key={item}>{item}</li>)}</ul>
        {index===1?<Link href="/register">Попробовать System Pro <span>→</span></Link>:<span className={styles.levelFoot}>Доступен в рабочем пространстве</span>}
      </article>)}</div>
    </section>

    <section className={styles.productSection} id="product">
      <div className={styles.sectionLabel}>ВОЗМОЖНОСТИ</div>
      <div className={styles.sectionHeader}><h2>Чат — только точка входа.<br/>Дальше работа становится системой.</h2><p>Сохраняйте контекст в проектах, подключайте поиск и файлы, автоматизируйте повторяемые процессы агентами и переходите к разработке внутри Dev Studio.</p></div>
      <div className={styles.bento}>{capabilityCards.map((item,index)=><article key={item.title} className={`${styles.bentoCard} ${styles[item.tone]} ${index===2?styles.bentoWide:""}`}>
        <span>{item.tag}</span><h3>{item.title}</h3><p>{item.text}</p><i>↗</i>
      </article>)}</div>
    </section>

    <section className={styles.platformSection}>
      <div className={styles.platformIntro}>
        <div className={styles.sectionLabel}>ОДНА ПЛАТФОРМА · ЧЕТЫРЕ УРОВНЯ РАБОТЫ</div>
        <h2>Начните с чата.<br/>Дойдите до автономного процесса.</h2>
        <p>Не нужно покупать четыре разных продукта. Используйте только тот уровень, который нужен задаче сейчас — остальные уже рядом.</p>
      </div>
      <div className={styles.platformRail}>
        <article><span>01 · AI CHAT</span><h3>Спросить и получить результат</h3><p>Тексты, файлы, web-поиск, документы, анализ и ежедневные рабочие задачи.</p><Link href="/register">Начать с чата →</Link></article>
        <article><span>02 · AGENT STUDIO</span><h3>Повторить процесс без ручной рутины</h3><p>Соберите workflow с AI, инструментами, интеграциями и расписанием.</p><Link href="/register">Создать агента →</Link></article>
        <article><span>03 · DEV STUDIO</span><h3>Делегировать многошаговую разработку</h3><p>План, код, GitHub, проверки и контроль исполнения внутри проекта.</p><Link href="/register">Открыть Dev Studio →</Link></article>
        <article><span>04 · API</span><h3>Встроить AI в собственный продукт</h3><p>Ключи, бюджеты, rate limits и контролируемое подключение к вашим приложениям.</p><Link href="/api">Посмотреть API →</Link></article>
      </div>
    </section>

    <section className={styles.workflowSection}>
      <div className={styles.workflowCopy}><div className={styles.sectionLabel}>КАК ЭТО РАБОТАЕТ</div><h2>Понятно с первого сообщения.</h2><p>Напишите задачу, выберите Авто / Простой / Средний / Сложный или конкретную нейросеть прямо рядом с кнопкой отправки. Остальная маршрутизация и контроль выполняются внутри.</p><Link href="/getting-started">Посмотреть начало работы →</Link></div>
      <div className={styles.workflowSteps}><article><b>01</b><div><h3>Опишите задачу</h3><p>Напишите запрос, прикрепите файл или продолжите работу внутри проекта.</p></div></article><article><b>02</b><div><h3>Выберите способ ответа</h3><p>Оставьте Авто, задайте уровень сложности или выберите конкретную нейросеть.</p></div></article><article><b>03</b><div><h3>Система выполняет работу</h3><p>Маршрутизация, поиск, контекст и расчёт стоимости происходят внутри.</p></div></article><article><b>04</b><div><h3>Масштабируйте удачный сценарий</h3><p>Оставьте его в проекте, перенесите в Agent Studio, Dev Studio или подключите через API.</p></div></article></div>
    </section>

    <section className={styles.useSection}>
      <div className={styles.sectionLabel}>СЦЕНАРИИ</div>
      <div className={styles.sectionHeader}><h2>Один продукт для работы, которая обычно разбросана по десятку сервисов.</h2><p>Начать можно с одного чата. Использовать глубже — по мере роста задач.</p></div>
      <div className={styles.useGrid}>{useCases.map(([title,text],index)=><article key={title}><span>0{index+1}</span><div><h3>{title}</h3><p>{text}</p></div></article>)}</div>
    </section>

    <section className={styles.economicsSection}>
      <div className={styles.economicsCopy}><span>ПРОЗРАЧНАЯ ЭКОНОМИКА</span><h2>Платите за работу,<br/>а не за набор подписок.</h2><p>Пополняйте единый рублёвый баланс. Система резервирует безопасный максимум, после ответа списывает фактическую стоимость и возвращает неиспользованный остаток.</p><Link href="/pricing">Как считается стоимость <b>→</b></Link></div>
      <div className={styles.moneyPanel}><div><small>ДО ЗАПРОСА</small><b>Резерв</b><span>Баланс не уходит в минус</span></div><div><small>ПОСЛЕ ОТВЕТА</small><b>Списание по факту</b><span>Видно в истории операций</span></div><div><small>ПРИ СБОЕ</small><b>Защита остатка</b><span>Незавершённая операция не считается успешной</span></div></div>
    </section>

    <section className={styles.trustSection}>
      <div className={styles.sectionLabel}>ДОВЕРИЕ К ОПЕРАЦИИ</div>
      <div className={styles.trustHead}>
        <h2>Главный продукт должен отвечать.<br/>Не показывать «модель доступна» на бумаге.</h2>
        <p>AIlegend проверяет рабочий маршрут перед клиентским использованием, умеет переходить на резервный канал и отделяет состояние ответа от внутреннего финансового учёта.</p>
      </div>
      <div className={styles.trustGrid}>
        <article><span>01</span><h3>До запуска</h3><p>Проверяется доступный маршрут, лимиты и безопасный максимум стоимости.</p></article>
        <article><span>02</span><h3>Во время работы</h3><p>Запрос имеет устойчивое состояние, а повторное подключение не должно создавать второй платный запуск.</p></article>
        <article><span>03</span><h3>После завершения</h3><p>Фиксируется фактический расход. Неиспользованный резерв возвращается в доступный баланс.</p></article>
        <article><span>04</span><h3>При сбое</h3><p>Незавершённая операция терминализируется и попадает в recovery-контур вместо вечного «зависло».</p></article>
      </div>
    </section>

    <section className={styles.apiSection}>
      <div className={styles.apiCard}><div className={styles.apiTop}><span>API</span><b>OpenAI-compatible</b></div><pre>{`POST /v1/chat/completions\nAuthorization: Bearer ••••••••\n\n{\n  "messages": [\n    {"role": "user", "content": "..."}\n  ]\n}`}</pre><div className={styles.apiStatus}><i/> API ready</div></div>
      <div className={styles.apiCopy}><div className={styles.sectionLabel}>ДЛЯ ПРОДУКТОВ И КОМАНД</div><h2>Встройте тот же контролируемый AI в свой продукт</h2><p>API-ключи, бюджеты, rate limits и история расходов — без отдельного зоопарка интеграций для каждого сценария.</p><ul><li>единый контроль доступа</li><li>лимиты и бюджеты</li><li>история расходов</li><li>совместимый API</li></ul><Link className={styles.darkCta} href="/register">Создать аккаунт <span>→</span></Link></div>
    </section>

    <section className={styles.faqSection} id="faq">
      <div className={styles.sectionLabel}>FAQ</div>
      <div className={styles.sectionHeader}><h2>До регистрации должно быть всё понятно</h2><p>Основные вопросы о режимах, оплате и рабочем процессе.</p></div>
      <div className={styles.faqList}>{faq.map(([question,answer])=><details key={question}><summary><span>{question}</span><b>＋</b></summary><p>{answer}</p></details>)}</div>
      <Link className={styles.faqMore} href="/faq">Все вопросы и ответы →</Link>
    </section>

    <section className={styles.finalCta}>
      <div className={styles.finalOrb} aria-hidden="true" />
      <span>НАЧНИТЕ С ОДНОЙ РЕАЛЬНОЙ ЗАДАЧИ</span><h2>Откройте чат.<br/>Выберите режим.<br/>Получите результат.</h2><p>А когда одного чата станет мало — проекты, агенты, Dev Studio, изображения и API уже находятся в том же аккаунте.</p>
      <div><Link className={styles.finalPrimary} href="/register">Создать аккаунт <b>↗</b></Link><Link className={styles.finalSecondary} href="/login">Уже есть аккаунт</Link></div><small>AIlegend · продукт BBTEC</small>
    </section>

    <footer className={styles.footer}>
      <div className={styles.footerBrand}><Link className={styles.brand} href="/"><span>✦</span><b>AIlegend</b></Link><p>AI-чат, проекты, агенты, разработка и API — в одном сервисе.</p><small>Продукт BBTEC</small></div>
      <div><b>Продукт</b><a href="#levels">System Lite / Pro / Max</a><Link href="/pricing">Стоимость</Link><Link href="/getting-started">Начало работы</Link><Link href="/status">Статус</Link></div>
      <div><b>Помощь</b><Link href="/faq">FAQ</Link><Link href="/app/help">Поддержка</Link><Link href="/forgot-password">Восстановить доступ</Link></div>
      <div><b>Документы</b><Link href="/legal/offer">Оферта</Link><Link href="/legal/privacy">Конфиденциальность</Link><Link href="/legal/refunds">Возвраты</Link><Link href="/legal/acceptable-use">Правила использования</Link></div>
      <div className={styles.footerBottom}><span>© {new Date().getFullYear()} AIlegend · BBTEC</span><span>Системные уровни скрывают внутреннюю техническую маршрутизацию и могут использовать разные подключённые AI-модели.</span></div>
    </footer>
  </main>;
}
