# Sprint 71 — Dev Studio production safety

Status: DONE / RUNTIME EVIDENCE

## Goal

Не позволить Dev Studio незаметно или необратимо изменить production/default branch.

## Реализовано

- только create/update proposals; delete не поддерживается;
- safe path, content/file-count limits;
- исходный файл обязан быть реально прочитан до update;
- immutable expected SHA и повторная проверка SHA;
- exact change proposal хранится внутри approval;
- `GET /agent-runs/<run_id>/changes-preview/` строит unified diff до подтверждения;
- write только после явного approval;
- sandbox validation до GitHub write;
- все изменения идут в отдельную `ai-workspace/run-*` branch;
- cancellation barriers до sandbox, branch creation и каждого write;
- QA & Security + Final Review после записи в ветку;
- PR создаётся только из точной ветки run;
- merge только с `confirm_merge=true`, с повторной проверкой GitHub head/base/SHA;
- новые commits после review блокируют merge;
- `abandon-branch` помечает ветку abandoned и честно сообщает, что remote branch не удалена; default branch не меняется;
- regression tests для tenant isolation и abandon safety.

## Runtime evidence

Проверить на тестовом repository: diff → reject/approve → isolated branch → QA → PR → explicit merge, а также cancellation/abandon без изменения default branch.
