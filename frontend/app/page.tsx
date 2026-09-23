import type {Metadata} from "next";
import Link from "next/link";
import styles from "./landing.module.css";

export const metadata: Metadata = {
  title: "AI Workspace — один AI для работы, документов, поиска и кода",
  description: "Работайте с AI без выбора из десятков моделей. System Lite, Pro и Max, проекты, документы, web-поиск, изображения, история и единый рублёвый баланс в одном сервисе.",
  openGraph: {
    title: "AI Workspace — один AI. Три уровня мощности.",
    description: "Выберите System Lite, Pro или Max и решайте задачи в одном рабочем пространстве: чаты, документы, проекты, web-поиск, изображения и API.",
    type: "website",
    locale: "ru_RU",
  },
};

const capabilities = [
  ["Работа с документами", "Загружайте PDF, DOCX, XLSX, CSV и текстовые файлы. Контекст остаётся рядом с задачей, а не теряется между чатами."],
  ["Проекты и длинный контекст", "Собирайте чаты, файлы и материалы по одной задаче в проекте и продолжайте работу с уже накопленным контекстом."],
  ["Поиск актуальной информации", "Когда нужны свежие данные, сервис может подключать web-поиск и возвращать результат вместе с источниками."],
  ["Изображения и анализ", "Создавайте изображения и анализируйте визуальные материалы в том же рабочем пространстве."],
  ["Сравнение вариантов", "Сравнивайте несколько подходов и сохраняйте лучший результат без ручного переноса текста между сервисами."],
  ["API для бизнеса", "Подключайте AI к своим продуктам через совместимый API с ключами, лимитами, бюджетами и контролем доступа."],
];

const useCases = [
  ["Маркетинг", "Посты, рекламные тексты, исследования, контент-планы, анализ конкурентов и гипотезы."],
  ["Код и разработка", "Разбор ошибок, архитектура, ревью, прототипы, документация и помощь в разработке."],
  ["Документы", "Разбор договоров, таблиц, отчётов, инструкций и больших массивов текста."],
  ["Исследования", "Сбор информации, сравнение вариантов, поиск источников и структурирование выводов."],
  ["Ежедневная работа", "Письма, идеи, планы, расчёты, резюме встреч и подготовка материалов."],
  ["Командные процессы", "Проекты, история, единый баланс и API для повторяемых рабочих сценариев."],
];

const faq = [
  ["Нужно ли разбираться в моделях и провайдерах?", "Нет. Для обычной работы достаточно выбрать уровень System Lite, System Pro или System Max. Внутренняя маршрутизация остаётся внутри сервиса."],
  ["Чем отличаются Lite, Pro и Max?", "Lite подходит для быстрых повседневных задач, Pro — универсальный режим по умолчанию, Max — для сложного анализа, разработки и задач, где важнее качество результата."],
  ["Есть ли обязательная подписка?", "Нет. Баланс пополняется вручную. Перед платным запросом система резервирует безопасный максимум, после ответа списывает фактическую стоимость и возвращает остаток резерва."],
  ["Можно ли работать с файлами и проектами?", "Да. Можно объединять чаты и материалы в проекты, загружать документы и использовать их как контекст для дальнейшей работы."],
  ["Что будет, если внешний AI-сервис временно недоступен?", "Система отслеживает состояние подключений и может использовать разрешённый резервный маршрут до начала ответа. Незавершённый запрос не должен превращаться в успешное платное списание."],
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
    <script type="application/ld+json" dangerouslySetInnerHTML={{__html: JSON.stringify(faqSchema)}} />

    <div className={styles.headerWrap}>
      <header className={styles.header}>
        <Link className={styles.brand} href="/" aria-label="AI Workspace — главная"><span>✦</span><b>AI Workspace</b></Link>
        <nav className={styles.nav} aria-label="Основная навигация">
          <a href="#levels">Уровни</a>
          <a href="#product">Возможности</a>
          <Link href="/pricing">Стоимость</Link>
          <a href="#faq">FAQ</a>
        </nav>
        <div className={styles.headerActions}>
          <Link className={styles.login} href="/login">Войти</Link>
          <Link className={styles.primarySmall} href="/register">Попробовать</Link>
        </div>
      </header>
    </div>

    <section className={styles.hero}>
      <div className={styles.heroGlow} aria-hidden="true" />
      <div className={styles.heroCopy}>
        <div className={styles.kicker}><span>●</span> AI ДЛЯ РЕАЛЬНОЙ РАБОТЫ, А НЕ ДЛЯ ВЫБОРА МОДЕЛЕЙ</div>
        <h1>Один AI для работы.<br/><span>Три уровня мощности.</span></h1>
        <p className={styles.heroLead}>Пишите тексты, разбирайте документы, ищите информацию, работайте с кодом и проектами. Вы выбираете только <strong>System Lite, Pro или Max</strong> — всё техническое сервис берёт на себя.</p>
        <div className={styles.heroActions}>
          <Link className={styles.primary} href="/register">Начать работу <span>→</span></Link>
          <a className={styles.secondary} href="#how">Посмотреть, как это работает</a>
        </div>
        <div className={styles.microTrust}><span>Без обязательной подписки</span><span>Оплата в рублях</span><span>История расходов</span></div>
      </div>

      <div className={styles.productStage} aria-label="Пример интерфейса AI Workspace">
        <div className={styles.windowBar}><span/><span/><span/><em>AI Workspace</em></div>
        <div className={styles.appMockup}>
          <aside className={styles.mockSidebar}>
            <div className={styles.mockNew}>＋ Новый чат</div>
            <small>ПРОЕКТЫ</small>
            <div className={styles.mockItem}>▣ Запуск продукта</div>
            <div className={styles.mockItem}>▣ Маркетинг</div>
            <div className={styles.mockItem}>▣ Документы</div>
            <small>ЧАТЫ</small>
            <div className={`${styles.mockItem} ${styles.mockActive}`}>Анализ стратегии</div>
            <div className={styles.mockItem}>Контент на неделю</div>
          </aside>
          <div className={styles.mockChat}>
            <div className={styles.mockTitle}><b>Анализ стратегии</b><span>•••</span></div>
            <div className={styles.mockMessages}>
              <div className={styles.mockUser}>Разбери мой план запуска и найди 5 самых опасных слабых мест.</div>
              <div className={styles.mockAnswer}>
                <div className={styles.answerHead}><span className={styles.spark}>✦</span><b>Ваш агент</b><em>System Pro</em></div>
                <p>В плане есть сильная идея, но до запуска я бы закрыл пять рисков:</p>
                <ol><li><b>Слишком широкий первый релиз.</b> Сократите MVP до одного главного сценария.</li><li><b>Нет измеримого критерия спроса.</b> Заранее определите метрику, после которой масштабируете продукт.</li><li><b>Экономика проверяется слишком поздно.</b> Рассчитайте стоимость одного активного пользователя до привлечения трафика.</li></ol>
              </div>
            </div>
            <div className={styles.mockComposer}>
              <span className={styles.plus}>＋</span><span className={styles.placeholder}>Продолжить диалог...</span><button type="button">System Pro⌄</button><span className={styles.send}>↑</span>
            </div>
          </div>
        </div>
        <div className={styles.floatingCard}><small>РЕЖИМ</small><b>System Pro</b><span>универсальный баланс качества и скорости</span></div>
      </div>
    </section>

    <section className={styles.proofStrip} aria-label="Ключевые возможности">
      <div><b>3</b><span>понятных уровня мощности</span></div>
      <div><b>1</b><span>рабочее пространство для задач</span></div>
      <div><b>₽</b><span>единый рублёвый баланс</span></div>
      <div><b>24/7</b><span>контроль состояния подключений</span></div>
    </section>

    <section className={styles.problemSection}>
      <div className={styles.problemIntro}><span className={styles.eyebrow}>ПРОЩЕ, ЧЕМ НАБОР НЕЙРОСЕТЕЙ</span><h2>Нейросеть должна решать задачу, а не создавать новую</h2></div>
      <div className={styles.beforeAfter}>
        <div className={styles.beforeCard}><small>Обычно</small><p>Выбирать сервис и модель</p><p>Переносить контекст между чатами</p><p>Следить за разными подписками</p><p>Разбираться в токенах и лимитах</p></div>
        <div className={styles.arrowCard}>→</div>
        <div className={styles.afterCard}><small>В AI Workspace</small><p><b>1.</b> Опишите задачу</p><p><b>2.</b> Выберите Lite, Pro или Max</p><p><b>3.</b> Получите результат и продолжайте работу</p><p><b>4.</b> Контекст и расходы остаются в одном месте</p></div>
      </div>
    </section>

    <section className={styles.section} id="levels">
      <div className={styles.sectionIntro}><span className={styles.eyebrow}>SYSTEM LEVELS</span><h2>Не список моделей. Понятный выбор мощности.</h2><p>Вместо технических названий — три режима, которые понятны до первого запроса.</p></div>
      <div className={styles.levelGrid}>
        <article className={styles.levelCard}><div className={styles.levelTop}><span>01</span><i>быстро</i></div><h3>System Lite</h3><p>Для простых и частых задач, где важны скорость и экономичность.</p><ul><li>короткие тексты и идеи</li><li>переформулировки и резюме</li><li>простые вопросы</li></ul></article>
        <article className={`${styles.levelCard} ${styles.levelFeatured}`}><div className={styles.recommended}>Рекомендуем начать здесь</div><div className={styles.levelTop}><span>02</span><i>универсально</i></div><h3>System Pro</h3><p>Основной режим для большинства рабочих задач: баланс качества, скорости и стоимости.</p><ul><li>маркетинг и исследования</li><li>документы и аналитика</li><li>код и рабочие проекты</li></ul><Link href="/register">Попробовать System Pro →</Link></article>
        <article className={styles.levelCard}><div className={styles.levelTop}><span>03</span><i>максимум</i></div><h3>System Max</h3><p>Для сложных задач, где важнее глубина анализа и качество результата.</p><ul><li>сложная разработка</li><li>многоэтапный анализ</li><li>задачи с высокой ценой ошибки</li></ul></article>
      </div>
    </section>

    <section className={styles.darkSection} id="how">
      <div className={styles.darkIntro}><span>КАК ЭТО РАБОТАЕТ</span><h2>От задачи до результата — без технической рутины</h2></div>
      <div className={styles.steps}>
        <article><b>01</b><h3>Пишите как человеку</h3><p>Задайте вопрос, прикрепите файл или продолжите работу в проекте.</p></article>
        <article><b>02</b><h3>Выберите уровень</h3><p>Lite для простого, Pro для большинства задач, Max для сложного.</p></article>
        <article><b>03</b><h3>Система выполняет задачу</h3><p>Маршрутизация, состояние подключений и расчёт стоимости происходят внутри.</p></article>
        <article><b>04</b><h3>Продолжайте с контекстом</h3><p>История, файлы, проекты и результаты остаются в рабочем пространстве.</p></article>
      </div>
    </section>

    <section className={styles.section} id="product">
      <div className={styles.sectionIntro}><span className={styles.eyebrow}>НЕ ТОЛЬКО ЧАТ</span><h2>Рабочее пространство, которое остаётся полезным после первого ответа</h2><p>AI встроен в нормальный рабочий процесс: с проектами, файлами, поиском, историей и контролем расходов.</p></div>
      <div className={styles.capabilityGrid}>{capabilities.map(([title,text],index)=><article key={title}><div className={styles.capIcon}>{String(index+1).padStart(2,"0")}</div><h3>{title}</h3><p>{text}</p></article>)}</div>
    </section>

    <section className={styles.useSection}>
      <div className={styles.sectionIntro}><span className={styles.eyebrow}>ДЛЯ ЧЕГО ИСПОЛЬЗОВАТЬ</span><h2>От одного вопроса до ежедневной рабочей среды</h2></div>
      <div className={styles.useGrid}>{useCases.map(([title,text])=><article key={title}><span>✦</span><div><h3>{title}</h3><p>{text}</p></div></article>)}</div>
    </section>

    <section className={styles.economics}>
      <div className={styles.economicsCopy}><span>ПРОЗРАЧНАЯ ЭКОНОМИКА</span><h2>Один баланс вместо набора обязательных подписок</h2><p>Пополняйте баланс вручную. Перед запросом система резервирует безопасный максимум, после ответа списывает фактическую стоимость и возвращает неиспользованный остаток.</p><Link className={styles.secondaryLight} href="/pricing">Как считается стоимость →</Link></div>
      <div className={styles.moneyFlow}><div><small>01 · ДО ЗАПРОСА</small><b>Резерв</b><p>Запрос не уводит баланс в минус.</p></div><div><small>02 · ПОСЛЕ ОТВЕТА</small><b>Списание по факту</b><p>В истории остаётся фактическая стоимость операции.</p></div><div><small>03 · ПРИ СБОЕ</small><b>Защита остатка</b><p>Незавершённая операция не должна считаться успешной.</p></div></div>
    </section>

    <section className={styles.trustSection}>
      <div className={styles.trustCopy}><span className={styles.eyebrow}>ДОВЕРИЕ К ПРОДУКТУ</span><h2>То, что пользователь обычно замечает только когда что-то сломалось, здесь проектируется заранее</h2></div>
      <div className={styles.trustGrid}><article><b>Защита от дублей</b><p>Повторный клик, обновление страницы или сетевой retry не должны превращаться в двойную платную операцию.</p></article><article><b>Контроль списаний</b><p>Пополнения, резервы, списания и возвраты фиксируются в истории баланса.</p></article><article><b>Стабильная маршрутизация</b><p>Система учитывает доступность подключений и не показывает пользователю внутреннюю техническую кухню.</p></article><article><b>Ваш рабочий контекст</b><p>Проекты, файлы и история помогают продолжать работу, а не начинать каждый запрос с нуля.</p></article></div>
    </section>

    <section className={styles.apiSection}>
      <div className={styles.codeCard}><div className={styles.codeTop}><span/><span/><span/><em>API</em></div><pre>{`POST /v1/chat/completions\nAuthorization: Bearer ••••••••\n\n{\n  "messages": [\n    {"role": "user", "content": "..."}\n  ]\n}`}</pre></div>
      <div><span className={styles.eyebrow}>ДЛЯ БИЗНЕСА И РАЗРАБОТЧИКОВ</span><h2>Тот же AI можно встроить в ваш продукт</h2><p>API-ключи, ограничения доступа, бюджеты, rate limits и единый контроль расходов — без отдельной пользовательской инфраструктуры для каждого AI-подключения.</p><Link className={styles.primary} href="/register">Создать аккаунт <span>→</span></Link></div>
    </section>

    <section className={styles.faqSection} id="faq">
      <div className={styles.sectionIntro}><span className={styles.eyebrow}>FAQ</span><h2>Что важно знать до регистрации</h2></div>
      <div className={styles.faqList}>{faq.map(([question,answer])=><details key={question}><summary>{question}<span>＋</span></summary><p>{answer}</p></details>)}</div>
      <Link className={styles.faqMore} href="/faq">Все вопросы и ответы →</Link>
    </section>

    <section className={styles.finalCta}>
      <div className={styles.finalGlow} aria-hidden="true" />
      <span>ПОПРОБУЙТЕ НА СВОЕЙ РЕАЛЬНОЙ ЗАДАЧЕ</span>
      <h2>Откройте чат. Выберите System Pro. И просто начните работать.</h2>
      <p>Без изучения моделей, сложных настроек и обязательной ежемесячной подписки.</p>
      <div><Link className={styles.primaryLight} href="/register">Создать аккаунт <span>→</span></Link><Link className={styles.secondaryDark} href="/login">Уже есть аккаунт</Link></div>
      <small>AI Workspace разработан компанией BBTEC</small>
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
