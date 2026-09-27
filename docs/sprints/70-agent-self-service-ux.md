# Sprint 70 — Agent Studio self-service UX

Status: DONE / RUNTIME EVIDENCE

## Goal

Пользователь создаёт и проверяет AI-сотрудника без технических знаний и без обращения в поддержку.

## Реализовано

- AI planner: описание обычным языком → безопасный preview структуры;
- preview показывает роль, цель, автономность, System level, workflow и стоимость проектирования;
- подтверждение создаёт только draft, ничего автоматически не запускается;
- шаблоны и visual workflow editor;
- readiness с понятными blocker/action сообщениями;
- безопасный `POST /agents/<id>/test/`: simulation без LLM, списаний и внешних действий;
- Test Mode показывает будущие шаги, approval checkpoints и заблокированные в тесте publish/notify;
- Agent Studio Test Mode UI;
- regression tests подтверждают zero paid calls / zero external actions и tenant isolation.

## Runtime evidence

- PostgreSQL regression tests;
- frontend production build/lint;
- ручной smoke: описание → preview → draft → test mode → readiness → activation → run.
