#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "KUBECONFIG is not set; refusing to use an implicit cluster context." >&2
  exit 1
fi

python_code="$(cat <<'PY'
from authentik.core.models import Application
from authentik.flows.models import (
    Flow,
    FlowAuthenticationRequirement,
    FlowDesignation,
    FlowStageBinding,
)
from authentik.policies.expression.models import ExpressionPolicy
from authentik.policies.models import PolicyBinding
from authentik.stages.prompt.models import Prompt, PromptStage
from authentik.stages.user_write.models import UserWriteStage


flow, _ = Flow.objects.update_or_create(
    slug="gramly-user-provisioning",
    defaults={
        "name": "Gramly user provisioning",
        "title": "Создать пользователя Gramly",
        "designation": FlowDesignation.ENROLLMENT,
        "authentication": FlowAuthenticationRequirement.REQUIRE_AUTHENTICATED,
        "compatibility_mode": True,
    },
)


def upsert_prompt(name, **defaults):
    prompt, _ = Prompt.objects.update_or_create(name=name, defaults=defaults)
    return prompt


prompts = [
    upsert_prompt(
        "gramly-provision-username", field_key="username", label="Рабочий логин",
        type="username", required=True, placeholder="например, maria",
        sub_text="Логин для входа во все рабочие сервисы Gramly.", order=10,
    ),
    upsert_prompt(
        "gramly-provision-name", field_key="name", label="Имя сотрудника",
        type="text", required=True, placeholder="Мария Волкова",
        sub_text="Отображаемое имя в рабочих сервисах.", order=20,
    ),
    upsert_prompt(
        "gramly-provision-email", field_key="email", label="Рабочий email",
        type="email", required=True, placeholder="maria@example.com",
        sub_text="Используется SSO-сервисами и восстановлением доступа.", order=30,
    ),
    upsert_prompt(
        "gramly-provision-telegram",
        field_key="attributes.gramly_crm_telegram_username",
        label="Telegram username для CRM", type="text", required=False,
        placeholder="@maria_gramly",
        sub_text="Необязательно. Должен точно совпадать с username существующего сотрудника в CRM.",
        order=40,
    ),
    upsert_prompt(
        "gramly-provision-password", field_key="password", label="Временный пароль",
        type="password", required=True, placeholder="",
        sub_text="Сотрудник обязательно сменит его при первом входе.", order=50,
    ),
    upsert_prompt(
        "gramly-provision-password-repeat", field_key="password_repeat",
        label="Повторите временный пароль", type="password", required=True,
        placeholder="", sub_text="", order=60,
    ),
    upsert_prompt(
        "gramly-provision-reset-password", field_key="attributes.reset_password",
        label="Require password reset", type="hidden", required=False, placeholder="",
        initial_value="return True", initial_value_expression=True, sub_text="", order=70,
    ),
]

validation, _ = ExpressionPolicy.objects.update_or_create(
    name="gramly-provisioning-input-validation",
    defaults={
        "expression": """data = request.context.get(\"prompt_data\", {})
raw = str(data.get(\"attributes.gramly_crm_telegram_username\") or \"\").strip()
username = raw.lstrip(\"@\").strip().lower()
allowed = \"abcdefghijklmnopqrstuvwxyz0123456789_\"
if username and (len(username) < 5 or len(username) > 32 or any(ch not in allowed for ch in username)):
    ak_message(\"Telegram username: 5–32 символа, только латинские буквы, цифры и _.\")
    return False
if data.get(\"password\") != data.get(\"password_repeat\"):
    ak_message(\"Временные пароли не совпадают.\")
    return False
data[\"attributes.gramly_crm_telegram_username\"] = username
return True""",
    },
)

prompt_stage, _ = PromptStage.objects.update_or_create(name="gramly-user-provisioning-prompt")
prompt_stage.fields.set(prompts)
password_prompt = PromptStage.objects.filter(name="default-password-change-prompt").first()
password_policies = list(password_prompt.validation_policies.all()) if password_prompt else []
prompt_stage.validation_policies.set([*password_policies, validation])

write_stage, _ = UserWriteStage.objects.update_or_create(
    name="gramly-user-provisioning-write",
    defaults={
        "user_creation_mode": "always_create",
        "create_users_as_inactive": False,
        "create_users_group": None,
        "user_type": "internal",
        "user_path_template": "users",
    },
)
prompt_binding, _ = FlowStageBinding.objects.update_or_create(
    target=flow, stage=prompt_stage,
    defaults={"order": 10, "evaluate_on_plan": True, "re_evaluate_policies": False},
)
write_binding, _ = FlowStageBinding.objects.update_or_create(
    target=flow, stage=write_stage,
    defaults={"order": 20, "evaluate_on_plan": True, "re_evaluate_policies": False},
)
FlowStageBinding.objects.filter(target=flow).exclude(
    pk__in=[prompt_binding.pk, write_binding.pk]
).delete()

access_policy, _ = ExpressionPolicy.objects.update_or_create(
    name="gramly-user-provisioning-admin-access",
    defaults={
        "expression": """if not request.user or request.user.is_anonymous:
    return False
return bool(
    request.user.is_superuser
    or request.user.groups.filter(name__in=[\"gramly-owners\", \"authentik Admins\"]).exists()
)""",
    },
)
PolicyBinding.objects.filter(target=flow).delete()
PolicyBinding.objects.create(
    target=flow, policy=access_policy, enabled=True, order=0, negate=False, timeout=30,
)

application, _ = Application.objects.update_or_create(
    slug="gramly-user-provisioning",
    defaults={
        "name": "Создать пользователя Gramly",
        "provider": None,
        "group": "Gramly administration",
        "meta_launch_url": "/if/flow/gramly-user-provisioning/",
        "meta_description": "Создание рабочей SSO-учётки и необязательная связь с Telegram CRM",
        "open_in_new_tab": False,
    },
)
application.policy_engine_mode = "any"
application.save(update_fields=["policy_engine_mode"])
PolicyBinding.objects.filter(target=application).delete()
PolicyBinding.objects.create(
    target=application, policy=access_policy, enabled=True, order=0, negate=False, timeout=30,
)

print("GRAMLY_RESULT user_provisioning_ready=true crm_link_field=true")
PY
)"

kubectl exec -n identity deployment/authentik-server -c server -- \
  ak shell -c "${python_code}" 2>&1 | \
  grep 'GRAMLY_RESULT user_provisioning_ready=true crm_link_field=true' >/dev/null

echo "Gramly user provisioning flow is ready at /if/flow/gramly-user-provisioning/."
