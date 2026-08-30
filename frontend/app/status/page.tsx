"use client";

import {useEffect, useState} from "react";
import styles from "./status.module.css";

type ComponentState = {name: string; status: string};
type Incident = {
  id: string;
  title: string;
  message: string;
  impact: string;
  state: string;
  affected_components: string[];
  updated_at: string;
};
type StatusPayload = {status: string; components: ComponentState[]; incidents: Incident[]};

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const labels: Record<string, string> = {
  operational: "Все системы работают штатно",
  degraded: "Наблюдаются частичные ограничения",
  major_outage: "Сервис временно недоступен",
  partial_outage: "Частичные ограничения",
  investigating: "Расследуется",
  monitoring: "Наблюдение",
  resolved: "Устранён",
};

export default function StatusPage() {
  const [data, setData] = useState<StatusPayload | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiBase}/status/`, {cache: "no-store", signal: controller.signal})
      .then((response) => {
        if (!response.ok) throw new Error("status unavailable");
        return response.json() as Promise<StatusPayload>;
      })
      .then(setData)
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setFailed(true);
      });
    return () => controller.abort();
  }, []);
  const state = failed ? "major_outage" : data?.status ?? "degraded";
  return <main className={styles.page}>
    <div className={styles.shell}>
      <div className={styles.brand}><i>✦</i>AI Workspace</div>
      <section className={styles.hero}>
        <h1>Статус сервиса</h1>
        <p>Актуальное состояние платформы и история публичных инцидентов.</p>
      </section>
      <section className={`${styles.summary} ${styles[state]}`} aria-live="polite">
        <span className={styles.dot}/>
        <div><strong>{labels[state]}</strong><small>Данные обновляются при открытии страницы</small></div>
      </section>
      <section className={styles.components} aria-label="Компоненты сервиса">
        {(data?.components ?? []).map((component) => <div className={styles.component} key={component.name}>
          <span>{component.name}</span><span>{labels[component.status] ?? component.status}</span>
        </div>)}
      </section>
      <section className={styles.incidents}>
        <h2>Инциденты</h2>
        {failed && <div className={styles.incident}><b>Не удалось получить статус</b><p>Команда уже может проверять доступность сервиса.</p></div>}
        {!failed && data?.incidents.length === 0 && <div className={styles.empty}>Зафиксированных инцидентов нет.</div>}
        {data?.incidents.map((incident) => <article className={styles.incident} key={incident.id}>
          <header><b>{incident.title}</b><small>{labels[incident.state] ?? incident.state}</small></header>
          <p>{incident.message}</p>
          <small>{incident.affected_components.join(" · ") || "Вся платформа"} · {new Date(incident.updated_at).toLocaleString("ru")}</small>
        </article>)}
      </section>
      <footer className={styles.footer}>Если проблема не отражена здесь, обратитесь в поддержку из личного кабинета.</footer>
    </div>
  </main>;
}
