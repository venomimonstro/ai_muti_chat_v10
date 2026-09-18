from dataclasses import dataclass

from apps.ai_registry.models import Provider

from .models import ComplianceSignoff


@dataclass(frozen=True)
class ComplianceItem:
    key: str
    title: str
    required_evidence: str


COMMERCIAL_COMPLIANCE_ITEMS = (
    ComplianceItem("entity-tax-regime", "Юридическое лицо и налоговый режим", "Реквизиты продавца и подтверждённая налоговая схема"),
    ComplianceItem("wallet-fiscalization", "Фискализация пополнения баланса", "Заключение по 54-ФЗ/чекам и production-настройка YooKassa"),
    ComplianceItem("receipt-refund-flow", "Чеки и возвраты", "Проверенный production сценарий payment + receipt + refund"),
    ComplianceItem("privacy-data-flow", "Персональные данные и трансграничные потоки", "Карта данных, сроки хранения и правовое основание обработки"),
    ComplianceItem("provider-commercial-terms", "Коммерческие условия AI-провайдеров", "Общий review процесса и договорной модели работы с AI-провайдерами"),
    ComplianceItem("public-legal-documents", "Публичные документы", "Оферта, политика конфиденциальности, возвраты, правила сервиса опубликованы на production-домене"),
    ComplianceItem("admin-mfa", "MFA администраторов", "MFA включена у всех platform admins и проверен recovery flow"),
    ComplianceItem("smtp-delivery", "Production email delivery", "Доказательство доставки verification и password-reset писем на реальный внешний адрес"),
    ComplianceItem("commercial-user-journey", "Коммерческий E2E пользователя", "JSON/лог успешного staging/production-like пути login → AI request → wallet update через scripts/commercial_http_smoke.py"),
    ComplianceItem("load-chaos-drill", "Load/chaos drill", "Артефакт нагрузочного/chaos прогона с проверкой отсутствия double billing и зависших reservations"),
)


def provider_terms_key(provider_slug: str) -> str:
    return f"provider-terms-{provider_slug}"[:100]


def required_compliance_keys() -> set[str]:
    keys = {item.key for item in COMMERCIAL_COMPLIANCE_ITEMS}
    keys.update(
        provider_terms_key(slug)
        for slug in Provider.objects.filter(enabled=True).values_list("slug", flat=True)
    )
    return keys


def bootstrap_compliance_manifest():
    created = 0
    for item in COMMERCIAL_COMPLIANCE_ITEMS:
        record, was_created = ComplianceSignoff.objects.get_or_create(
            key=item.key,
            defaults={"title": item.title, "notes": item.required_evidence},
        )
        if not was_created and not record.notes:
            record.notes = item.required_evidence
            record.save(update_fields=["notes", "updated_at"])
        created += int(was_created)
    for provider in Provider.objects.filter(enabled=True).order_by("slug"):
        _record, was_created = ComplianceSignoff.objects.get_or_create(
            key=provider_terms_key(provider.slug),
            defaults={
                "title": f"Коммерческие условия провайдера {provider.name}",
                "notes": (
                    "Приложите ссылку/договор/переписку, подтверждающие допустимость текущей "
                    "схемы коммерческого API-доступа, обработки пользовательских данных и биллинга."
                ),
            },
        )
        created += int(was_created)
    return created
