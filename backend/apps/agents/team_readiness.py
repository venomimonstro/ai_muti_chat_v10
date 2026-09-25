from django.core.exceptions import ValidationError

from .models import AgentTeam
from .runtime import _model_for


def team_readiness(team: AgentTeam):
    blockers = []
    warnings = []
    checks = {}

    checks["active"] = bool(team.active)
    if not team.active:
        blockers.append("Команда приостановлена")

    objective_ok = bool(str(team.objective or "").strip())
    checks["objective"] = objective_ok
    if not objective_ok:
        blockers.append("Задайте общую цель команды")

    members = list(team.members.filter(enabled=True).select_related("agent").order_by("priority", "role"))
    checks["members"] = bool(members)
    if not members:
        blockers.append("В команде нет активных участников")
        return {"ready": False, "checks": checks, "blockers": blockers, "warnings": warnings, "models": []}

    director_member = next((member for member in members if member.agent_id == team.director_id), None)
    checks["director"] = director_member is not None
    if director_member is None:
        blockers.append("Руководитель команды отключён или отсутствует среди участников")

    if team.max_cost_rub_per_run <= 0:
        blockers.append("Лимит стоимости запуска команды должен быть больше нуля")
    if team.max_handoffs < 1:
        blockers.append("Лимит передач между участниками должен быть больше нуля")

    models = []
    for member in members:
        if member.agent.status != member.agent.Status.ACTIVE:
            blockers.append(f"{member.role}: сотрудник «{member.agent.name}» приостановлен")
            continue
        try:
            model = _model_for(member.agent)
            models.append({"role": member.role, "level": member.agent.system_level, "model": model.slug})
        except ValidationError as exc:
            blockers.append(f"{member.role}: {exc}")

    checks["models"] = len(models) == len(members)

    if team.kind == AgentTeam.Kind.DEVELOPMENT:
        project = team.project
        checks["project"] = project is not None
        if project is None:
            blockers.append("Dev Team не привязана к проекту")
        else:
            try:
                binding = project.github_repository
            except Exception:
                binding = None
            github_ok = bool(binding and binding.installation_id and binding.installation.active)
            checks["github_binding"] = github_ok
            if not binding:
                blockers.append("К проекту не подключён GitHub repository")
            elif not binding.installation.active:
                blockers.append("GitHub App installation отключена")
            elif not binding.write_enabled:
                warnings.append("GitHub доступен только для чтения: анализ возможен, запись изменений будет заблокирована")

    return {
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "models": models,
    }


def require_team_ready(team: AgentTeam):
    result = team_readiness(team)
    if not result["ready"]:
        raise ValidationError("; ".join(result["blockers"]))
    return result
