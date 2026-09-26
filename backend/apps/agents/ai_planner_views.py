import json
import re
import uuid
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.token_estimator import estimate_message_tokens
from apps.billing.pricing import active_price, quote, require_margin
from apps.billing.services import release, reserve, settle
from apps.projects.models import Project

from .accounting import (
    release_agent_provider_spend,
    reserve_agent_provider_spend,
    settle_agent_provider_spend,
)
from .config_views import _validate_graph
from .models import Agent
from .planner import infer_kind
from .readiness import RUNTIME_NODE_TYPES
from .runtime import _model_for
from .serializers import AgentSerializer


PLANNER_OUTPUT_TOKENS = 1800
PLANNER_ALLOWED_TYPES = set(RUNTIME_NODE_TYPES)
AUTONOMY = {"controlled", "semi_autonomous", "autonomous"}
LEVELS = {"economy", "balanced", "maximum"}


def _extract_json(text):
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValidationError({"detail": "AI-конструктор вернул некорректную структуру. Повторите запрос."})
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValidationError({"detail": "AI-конструктор вернул некорректный JSON. Повторите запрос."}) from exc


def _messages(description):
    system = (
        "Ты проектировщик no-code AI-сотрудников Agent Studio. Верни ТОЛЬКО один JSON-объект без markdown. "
        "Не выполняй задачу пользователя — спроектируй сотрудника, который сможет её выполнять. "
        "Разрешённые типы узлов: llm, research, web, files, image, review, analytics, condition, approval, wait, notify, publish, finish. "
        "Запрещены github, code, shell, sandbox и любые произвольные инструменты. "
        "Внешняя публикация по умолчанию должна идти через approval и publish со status=draft. "
        "Не создавай циклы: все переходы только вперёд. Максимум 12 узлов. "
        "JSON schema: {name:string, role:string, objective:string, instructions:string, autonomy:'controlled|semi_autonomous|autonomous', "
        "system_level:'economy|balanced|maximum', graph:{version:1,nodes:[{id,title,type,prompt?,status?,condition_source?,operator?,value?,on_true?,on_false?,wait_minutes?,notification_title?,message?}],edges:[{from,to}]}}. "
        "Используй понятные русские названия шагов. Делай минимальную достаточную карту, не раздувай количество LLM-шагов."
    )
    user = f"Спроектируй AI-сотрудника по описанию:\n{description[:12000]}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _sanitize_draft(payload, description):
    if not isinstance(payload, dict):
        raise ValidationError({"detail": "AI-конструктор вернул неверный формат"})
    graph = _validate_graph(payload.get("graph") or {})
    node_types = {str(node.get("type") or "").lower() for node in graph["nodes"]}
    unsupported = sorted(node_types - PLANNER_ALLOWED_TYPES)
    if unsupported:
        raise ValidationError({"detail": "AI-конструктор предложил неподдерживаемые шаги: " + ", ".join(unsupported)})

    autonomy = str(payload.get("autonomy") or "semi_autonomous").strip().lower()
    if autonomy not in AUTONOMY:
        autonomy = "semi_autonomous"
    level = str(payload.get("system_level") or "balanced").strip().lower()
    if level not in LEVELS:
        level = "balanced"

    tools = {}
    if node_types & {"web", "research"}:
        tools["web"] = True
    if "files" in node_types:
        tools["files"] = True
    if "image" in node_types:
        tools["images"] = True
    if "publish" in node_types:
        # AI preview may never silently grant autonomous publishing.
        tools["publish"] = "approval"
        if autonomy == "autonomous":
            autonomy = "semi_autonomous"

    name = str(payload.get("name") or "AI-сотрудник").strip()[:160] or "AI-сотрудник"
    role = str(payload.get("role") or "AI specialist").strip()[:160] or "AI specialist"
    objective = str(payload.get("objective") or description).strip()[:12000] or str(description).strip()[:12000]
    instructions = str(payload.get("instructions") or "").strip()[:12000]
    return {
        "name": name,
        "role": role,
        "objective": objective,
        "instructions": instructions,
        "autonomy": autonomy,
        "system_level": level,
        "tool_policy": tools,
        "graph": graph,
    }


def _project_for(user, raw_id):
    if not raw_id:
        return None
    project = Project.objects.filter(pk=raw_id, owner=user, archived_at__isnull=True).first()
    if project is None:
        raise ValidationError({"project": "Проект недоступен"})
    return project


class AgentAIPlannerPreviewView(APIView):
    def post(self, request):
        description = str(request.data.get("description") or "").strip()
        if len(description) < 20:
            raise ValidationError({"description": "Опишите сотрудника чуть подробнее"})
        if infer_kind(description) == "development":
            raise ValidationError({"description": "Для программирования и GitHub используйте Dev Studio — там действует отдельный безопасный runtime."})

        operation_id = uuid.uuid4().hex
        customer = None
        provider_reservation = None
        try:
            model = _model_for(SimpleNamespace(system_level="balanced"))
            messages = _messages(description)
            max_output = min(PLANNER_OUTPUT_TOKENS, model.max_output_tokens)
            estimated_input = max(64, estimate_message_tokens(messages) + 32)
            price = active_price(model.slug)
            estimate = require_margin(
                quote(
                    price,
                    estimated_input,
                    max_output,
                    provider_slug=model.provider.slug,
                    model_slug=model.slug,
                    operation_type="agent",
                )
            )
            source_key = f"agent-planner:{operation_id}"
            customer = reserve(request.user, estimate.user_charge_rub, source_key)
            provider_reservation = reserve_agent_provider_spend(
                model=model,
                provider_cost_rub=estimate.provider_cost_rub,
                fx_snapshot=estimate.fx_snapshot,
                source_key=source_key,
            )
            result = adapter_for(model).generate(
                model=model.upstream_model or model.slug,
                messages=messages,
                max_output_tokens=max_output,
            )
            actual_quote = require_margin(
                quote(
                    price,
                    max(1, result.input_tokens),
                    max(1, result.output_tokens),
                    provider_slug=model.provider.slug,
                    model_slug=model.slug,
                    operation_type="agent",
                )
            )
            actual = min(Decimal(actual_quote.user_charge_rub), Decimal(customer.amount_rub))
            settle_agent_provider_spend(
                reservation=provider_reservation,
                model=model,
                result=result,
                actual_quote=actual_quote,
                source_id=operation_id,
                customer_charge=actual,
            )
            provider_reservation = None
            settle(customer.id, actual)
            customer = None

            payload = _extract_json(result.text)
            draft = _sanitize_draft(payload, description)
            return Response(
                {
                    "draft": draft,
                    "cost_rub": str(actual),
                    "system_level": "System Pro",
                    "operation_id": operation_id,
                }
            )
        except ProviderError as exc:
            raise ValidationError({"detail": f"AI-конструктор временно недоступен: {exc}"}) from exc
        except DjangoValidationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        finally:
            if customer is not None:
                try:
                    release(customer.id)
                except Exception:
                    pass
            if provider_reservation is not None:
                release_agent_provider_spend(provider_reservation)


class AgentAIPlannerCreateView(APIView):
    @transaction.atomic
    def post(self, request):
        raw = request.data.get("draft")
        description = str(request.data.get("description") or (raw or {}).get("objective") or "").strip()
        if not isinstance(raw, dict):
            raise ValidationError({"draft": "Сначала получите preview AI-конструктора"})
        draft = _sanitize_draft(raw, description)
        project = _project_for(request.user, request.data.get("project"))
        agent = Agent.objects.create(
            owner=request.user,
            project=project,
            name=draft["name"],
            role=draft["role"],
            objective=draft["objective"],
            instructions=draft["instructions"],
            autonomy=draft["autonomy"],
            system_level=draft["system_level"],
            tool_policy=draft["tool_policy"],
            graph=draft["graph"],
            status=Agent.Status.DRAFT,
        )
        return Response(AgentSerializer(agent, context={"request": request}).data, status=status.HTTP_201_CREATED)
