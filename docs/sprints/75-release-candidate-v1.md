# Sprint 75 — Release Candidate v1.0

Status: READY TO RUN

## Goal

Одна команда должна дать проверяемый ответ: Agent Studio / Dev Studio можно выпускать коммерчески или запуск заблокирован.

## Реализовано

- `scripts/release_check.sh`: PostgreSQL tests, security, commercial limits, Agent self-service, Dev safety, diagnostics, audits, frontend build/lint/runtime smoke;
- `scripts/agent_runtime_drill.sh`: restart/recovery drill worker + beat;
- существующий `scripts/commercial_launch_check.sh`: system health, economics, procurement/FX, client E2E, live paid AI, B2B API/billing, commercial audit и evidence;
- новый `scripts/agent_release_candidate_check.sh` последовательно запускает runtime drill + commercial launch gate + финальные Agent/Dev audits;
- evidence logs сохраняются в `launch-evidence/` и получают SHA-256;
- PASS невозможен при проваленном release check, runtime drill, live E2E или production audit.

## Финальный запуск

```bash
cd /opt/ai-workspace
sudo bash scripts/update.sh
sudo bash scripts/agent_release_candidate_check.sh
```

Commercial v1.0 готов только если последняя команда завершилась строкой:

`AGENT RELEASE CANDIDATE v1.0: PASS`

До этого момента статус продукта — release candidate, а не подтверждённый production launch.
