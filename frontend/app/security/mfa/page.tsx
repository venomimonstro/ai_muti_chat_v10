"use client";

import Link from "next/link";
import {FormEvent, useEffect, useState} from "react";
import {api} from "../../../lib/api";
import styles from "../../commercial.module.css";

type Status = {
  available: boolean;
  enabled: boolean;
  session_verified: boolean;
  recovery_codes_remaining: number;
};

export default function MFAPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [secret, setSecret] = useState("");
  const [uri, setUri] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [error, setError] = useState("");

  const load = () =>
    api<Status>("/auth/mfa/status/")
      .then(setStatus)
      .catch((reason) =>
        setError(reason instanceof Error ? reason.message : "Не удалось получить статус MFA"),
      );

  useEffect(() => {
    void load();
  }, []);

  const setup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);

    try {
      const result = await api<{secret: string; otpauth_uri: string}>("/auth/mfa/setup/", {
        method: "POST",
        body: JSON.stringify({password: form.get("password")}),
      });
      setSecret(result.secret);
      setUri(result.otpauth_uri);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось начать настройку");
    }
  };

  const confirm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);

    try {
      const result = await api<{enabled: boolean; recovery_codes: string[]}>("/auth/mfa/confirm/", {
        method: "POST",
        body: JSON.stringify({code: form.get("code")}),
      });
      setCodes(result.recovery_codes);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Неверный код");
    }
  };

  const verify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    const code = String(form.get("code") || "").trim();
    const recovery = String(form.get("recovery") || "").trim();

    try {
      await api("/auth/mfa/verify/", {
        method: "POST",
        body: JSON.stringify(code ? {code} : {recovery_code: recovery}),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "MFA не подтверждена");
    }
  };

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <header className={styles.top}>
          <div className={styles.brand}>AI Workspace · Security</div>
          <Link href="/app/account">Назад в аккаунт</Link>
        </header>

        <section className={styles.hero}>
          <h1>Двухфакторная защита</h1>
          <p>
            TOTP доступна каждому пользователю. Для администраторов MFA может быть обязательной политикой
            доступа к Admin Console. Recovery codes показываются только после успешного подтверждения.
          </p>
        </section>

        {error && <div className={styles.notice}>{error}</div>}

        {status?.enabled && !codes.length && (
          <div className={styles.card}>
            <h2>MFA включена</h2>
            <p>
              Текущая сессия: {status.session_verified ? "подтверждена" : "требует MFA"}. Осталось recovery
              codes: {status.recovery_codes_remaining}.
            </p>
          </div>
        )}

        {status?.enabled && !status.session_verified && (
          <form className={styles.card} onSubmit={verify} style={{marginTop: 16, maxWidth: 560}}>
            <h2>Подтвердите текущую сессию</h2>
            <label>
              TOTP-код
              <input
                name="code"
                inputMode="numeric"
                pattern="[0-9]{6}"
                style={{display: "block", width: "100%", padding: 12, marginTop: 8}}
              />
            </label>
            <p className={styles.muted}>Или используйте одноразовый recovery code:</p>
            <input name="recovery" placeholder="XXXXXXXX-XXXXXXXX" style={{width: "100%", padding: 12}} />
            <button className={styles.cta} style={{border: 0, marginTop: 14}}>
              Подтвердить
            </button>
          </form>
        )}

        {status?.available && !status.enabled && !secret && (
          <form className={styles.card} onSubmit={setup} style={{maxWidth: 560}}>
            <h2>1. Подтвердите пароль</h2>
            <input
              name="password"
              type="password"
              required
              autoComplete="current-password"
              style={{width: "100%", padding: 12}}
            />
            <button className={styles.cta} style={{border: 0, marginTop: 14}}>
              Начать настройку
            </button>
          </form>
        )}

        {secret && !status?.enabled && (
          <>
            <div className={styles.card}>
              <h2>2. Добавьте TOTP</h2>
              <p>
                Секрет: <code>{secret}</code>
              </p>
              <p className={styles.muted}>Добавьте этот ключ в совместимое приложение-аутентификатор.</p>
              <code style={{wordBreak: "break-all"}}>{uri}</code>
            </div>
            <form className={styles.card} onSubmit={confirm} style={{marginTop: 16, maxWidth: 560}}>
              <h2>3. Введите шестизначный код</h2>
              <input
                name="code"
                inputMode="numeric"
                pattern="[0-9]{6}"
                required
                style={{width: "100%", padding: 12}}
              />
              <button className={styles.cta} style={{border: 0, marginTop: 14}}>
                Включить MFA
              </button>
            </form>
          </>
        )}

        {codes.length > 0 && (
          <div className={styles.card}>
            <h2>Сохраните recovery codes</h2>
            <p>Каждый код одноразовый. После закрытия страницы они повторно не показываются.</p>
            <pre>{codes.join("\n")}</pre>
          </div>
        )}
      </div>
    </main>
  );
}
