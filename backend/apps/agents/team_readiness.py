from django.core.exceptions import ValidationError

from .models import AgentTeam
from .runtime import _model_for
from .sandbox_client import sandbox_enabled


PUBLIC_LEVELS = {
    "economy": "System Lite",
    "balanced": "System Pro",
    "maximum": "System Max",
}

DEV_REQUIRED_ROLES = {
    "Engineering Director",
    "Architecture",
    "Development",
    "QA & Security",
}


def team_readiness(team: AgentTeam):
    blockers = []
    warnings = []
    checks = {}
    actions = []
    action_codes = set()

    def add_action(code, label, href=""):
        if code in action_codes:
            return
        action_codes.add(code)
        actions.append({"code": code, "label": label, "href": href})

    checks["active"] = bool(team.active)
    if not team.active:
        blockers.append("Команда приостановлена")
        add_action("activate_team", "Включите команду перед запуском")

    objective_ok = bool(str(team.objective or "").strip())
    checks["objective"] = objective_ok
    if not objective_ok:
        blockers.append("Задайте общую цель команды")
        add_action("set_objective", "Заполните общую цель команды")

    members = list(team.members.filter(enabled=True).select_related("agent").order_by("priority", "role"))
    checks["members"] = bool(members)
    if not members:
        blockers.append("В команде нет активных участников")
        add_action("add_members", "Добавьте хотя бы одного активного участника")
        return {
            "ready": False,
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
            "actions": actions,
            "models": [],
        }

    director_member = next((member for member in members if member.agent_id == team.director_id), None)
    checks["director"] = director_member is not None
    if director_member is None:
        blockers.append("Руководитель команды отключён или отсутствует среди участников")
        add_action("choose_director", "Назначьте активного руководителя команды")

    if team.max_cost_rub_per_run <= 0:
        blockers.append("Лимит стоимости запуска команды должен быть больше нуля")
        add_action("fix_budget", "Установите положительный бюджет запуска")
    if team.max_handoffs < 1:
        blockers.append("Лимит передач между участниками должен быть больше нуля")
        add_action("fix_handoffs", "Увеличьте допустимое число передач между участниками")

    models = []
    for member in members:
        public_level = PUBLIC_LEVELS.get(member.agent.system_level, "System Pro")
        if member.agent.status != member.agent.Status.ACTIVE:
            blockers.append(f"{member.role}: сотрудник «{member.agent.name}» приостановлен")
            add_action("activate_members", "Активируйте всех участников команды")
            continue
        try:
            _model_for(member.agent)
            models.append({"role": member.role, "level": member.agent.system_level, "model": public_level})
        except ValidationError as exc:
            blockers.append(f"{member.role}: {exc}")
            add_action(
                "model_unavailable",
                "Один из уровней System недоступен. Администратору нужно проверить маршрутизацию моделей",
            )

    checks["models"] = len(models) == len(members)

    if team.kind == AgentTeam.Kind.DEVELOPMENT:
        roles = {str(member.role or "").strip() for member in members}
        missing_roles = sorted(DEV_REQUIRED_ROLES - roles)
        checks["dev_roles"] = not missing_roles
        if missing_roles:
            blockers.append("Dev Team неполная: отсутствуют роли " + ", ".join(missing_roles))
            add_action("repair_dev_team", "Восстановите стандартный состав Dev Team")

        project = team.project
        checks["project"] = project is not None
        if project is None:
            blockers.append("Dev Team не привязана к проекту")
            add_action("attach_project", "Выберите проект для Dev Team", "/app/projects")
        else:
            try:
                binding = project.github_repository
            except Exception:
                binding = None

            github_binding_ok = bool(binding and binding.installation_id and binding.installation.active)
            checks["github_binding"] = github_binding_ok
            if not binding:
                blockers.append("К проекту не подключён GitHub repository")
                add_action("connect_github", "Подключите GitHub к проекту", f"/app/projects/{project.id}/github")
            elif not binding.installation.active:
                blockers.append("GitHub App installation отключена")
                add_action("repair_github", "Переподключите GitHub", f"/app/projects/{project.id}/github")
            else:
                contents_permission = str((binding.installation.permissions or {}).get("contents") or "").lower()
                provider_write_ok = contents_permission in {"write", "admin"}
                app_write_ok = bool(binding.write_enabled)
                checks["github_provider_write"] = provider_write_ok
                checks["github_write_enabled"] = app_write_ok
                if not provider_write_ok:
                    blockers.append("GitHub App не имеет права contents:write для этого repository")
                    add_action("repair_github_permissions", "Переподключите GitHub с правом записи", f"/app/projects/{project.id}/github")
                if not app_write_ok:
                    blockers.append("Запись Dev Studio в рабочую ветку GitHub отключена")
                    add_action("enable_github_write", "Разрешите запись в рабочую ветку GitHub", f"/app/projects/{project.id}/github")

        sandbox_ok = sandbox_enabled()
        checks["sandbox"] = sandbox_ok
        if not sandbox_ok:
            blockers.append("Sandbox для безопасной проверки кода не настроен")
            add_action("configure_sandbox", "Администратору нужно включить изолированный sandbox")

    return {
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "actions": actions,
        "models": models,
    }


def require_team_ready(team: AgentTeam):
    result = team_readiness(team)
    if not result["ready"]:
        raise ValidationError("; ".join(result["blockers"]))
    return result
