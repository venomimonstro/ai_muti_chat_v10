"use client";
import {FormEvent, useState} from "react";

type Message = {role: "user" | "assistant"; content: string};

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [value, setValue] = useState("");
  const send = (event: FormEvent) => {
    event.preventDefault();
    const prompt = value.trim();
    if (!prompt) return;
    setMessages((current) => [...current, {role: "user", content: prompt},
      {role: "assistant", content: "API готов к подключению. Войдите в аккаунт и создайте диалог."}]);
    setValue("");
  };
  return <main className="shell">
    <aside>
      <div className="brand"><span>AI</span> Workspace</div>
      <button className="newChat">＋ Новый чат</button>
      <nav><a className="active">Сегодня</a><a>Проекты</a><a>Файлы</a><a>Поиск</a></nav>
      <div className="wallet"><small>Баланс</small><strong>25,00 ₽</strong><button>Пополнить</button></div>
    </aside>
    <section className="chat">
      <header><button className="model">AUTO · Баланс⌄</button><div className="status">● Все системы работают</div></header>
      <div className="thread">
        {messages.length === 0 ? <div className="hero"><div className="spark">✦</div><h1>Чем займёмся?</h1>
          <p>Одна история и память для лучших AI-моделей.</p><div className="suggestions">
          <button onClick={() => setValue("Составь маркетинговую стратегию")}>Маркетинговая стратегия</button>
          <button onClick={() => setValue("Проанализируй документ")}>Анализ документа</button>
          <button onClick={() => setValue("Помоги написать код")}>Разработка</button></div></div> :
          messages.map((message, index) => <article key={index} className={message.role}>
            <b>{message.role === "user" ? "Вы" : "AI Workspace"}</b><p>{message.content}</p></article>)}
      </div>
      <form onSubmit={send} className="composer"><textarea aria-label="Сообщение" value={value}
        onChange={(e) => setValue(e.target.value)} placeholder="Напишите сообщение…"/><div className="actions">
        <button type="button" title="Прикрепить файл">＋</button><span>≈ до 2,00 ₽</span><button className="send">↑</button></div></form>
      <footer>Стоимость рассчитывается по фактическому использованию · Ошибки не оплачиваются</footer>
    </section>
  </main>;
}

