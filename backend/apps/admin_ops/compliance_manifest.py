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
)


def provider_terms_key(provider_slug: str) -> str:
    return f"provider-terms-{provider_slug}"[:100]


def required_compliance_keys() -> set[str]:
    keys = {item.key for item in COMMERCIAL_COMPLIANCE_ITEMS}
    keys.update(provider_terms_key(slug) for slug in Provider.objects.filter(enabled=True).values_list("slug", flat=True))
    return keys


def bootstrap_compliance_manifest():
    created = 0
    for item in COMMERCIAL_COMPLIANCE_ITEMS:
        _record, was_created = ComplianceSignoff.objects.get_or_create(
            key=item.key,
            defaults={"title": item.title, "notes": item.required_evidence},
        )
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
