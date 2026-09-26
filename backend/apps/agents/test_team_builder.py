from .team_builder import infer_team_kind, needs_extended_team, team_draft


def test_simple_content_team_uses_three_core_roles():
    draft = team_draft("Писать посты для блога и проверять текст перед публикацией")

    assert draft["kind"] == "content"
    assert draft["size_mode"] == "compact"
    assert len(draft["members"]) == 3
    assert draft["members"][0]["role"] == "Главный редактор"
    assert {item["role"] for item in draft["members"]} == {"Главный редактор", "Копирайтер", "Редактор"}


def test_complex_marketing_team_adds_research_and_strategy_roles():
    description = (
        "Нужен маркетинговый отдел: исследовать рынок и конкурентов, провести аналитику, "
        "разработать стратегию, контент-план и подготовить материалы для нескольких каналов"
    )
    draft = team_draft(description)

    assert draft["kind"] == "marketing"
    assert draft["size_mode"] == "extended"
    assert len(draft["members"]) == 5
    roles = {item["role"] for item in draft["members"]}
    assert "Исследователь рынка" in roles
    assert "Контент-стратег" in roles


def test_long_ui_boilerplate_does_not_alone_force_extended_team():
    description = (
        "Писать письма клиентам и проверять их. "
        "Контекст бизнеса: небольшая компания. "
        "Режим работы: обычные внутренние задачи выполнять самостоятельно. "
        "Правило подтверждений: публикации, отправки и другие внешние действия подтверждать у меня."
    )

    assert needs_extended_team(description) is False
    assert team_draft(description)["size_mode"] == "compact"


def test_development_is_routed_to_dev_studio():
    draft = team_draft("Разрабатывать backend и исправлять код в GitHub")

    assert infer_team_kind("Разрабатывать backend и исправлять код в GitHub") == "development"
    assert draft["kind"] == "development"
    assert draft["members"] == []
    assert draft["size_mode"] == "dev"
