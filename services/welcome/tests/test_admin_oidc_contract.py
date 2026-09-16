import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("direct,active,groups,allowed", [
    (True, True, ["Employees"], True),
    (False, True, ["Employees"], False),
    (False, True, ["Product"], True),
    (True, False, ["Employees"], False),
])
def test_provider_mapping_honours_only_existing_enabled_grant(
    monkeypatch: pytest.MonkeyPatch, direct: bool, active: bool, groups: list[str], allowed: bool,
) -> None:
    script = (Path(__file__).resolve().parents[3] / "infra/kubernetes/apps/identity/configure-welcome-admin-oidc.sh").read_text()
    expressions = re.findall(r"'expression': '''(.*?)'''", script, re.S)
    app = object()
    user = SimpleNamespace(is_active=active, email="test@example.org", groups=SimpleNamespace(values_list=lambda *a, **kw: groups))
    bindings = Mock()
    bindings.filter.return_value.exists.return_value = direct
    for name, attr, value in (
        ("authentik.core.models", "Application", SimpleNamespace(objects=SimpleNamespace(get=lambda **kw: app))),
        ("authentik.policies.models", "PolicyBinding", SimpleNamespace(objects=bindings)),
    ):
        module = ModuleType(name)
        setattr(module, attr, value)
        monkeypatch.setitem(sys.modules, name, module)
    results = []
    for expression in expressions:
        namespace: dict[str, Any] = {}
        exec("def mapping(request):\n" + "\n".join("    " + line for line in expression.splitlines()), namespace)
        results.append(namespace["mapping"](SimpleNamespace(user=user)))
    assert bool(results[0].get("email_verified")) is allowed
    assert ("gramly-hello-admin" in results[1]["groups"]) is (direct and active)
    for call in bindings.filter.call_args_list:
        assert call.kwargs == dict(target=app, user=user, enabled=True, negate=False, policy__isnull=True, group__isnull=True)
    assert "gramly-hello-admin" not in groups
