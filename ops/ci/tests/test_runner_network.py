import base64
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("runner_network", ROOT / "ops/ci/reconcile_runner_network.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RunnerNetworkTests(unittest.TestCase):
    def test_existing_credentials_and_other_settings_are_preserved(self):
        config = 'container:\n  network: bridge\n  privileged: false\nserver:\n  connections:\n    gramly:\n      uuid: example-id\n      token: synthetic-secret\n'
        secret = {"data": {"config.yml": base64.b64encode(config.encode()).decode(), "other": "unchanged"}}
        patch = module.secret_patch(secret)
        result = base64.b64decode(patch["data"]["config.yml"]).decode()
        self.assertEqual(result, config.replace("network: bridge", 'network: ""'))
        self.assertEqual(list(patch["data"]), ["config.yml"])
        self.assertIsNone(module.secret_patch({"data": patch["data"]}))

    def test_missing_network_is_inserted_in_container_only(self):
        config = "runner:\n  network: untouched\ncontainer:\n  privileged: false\nserver:\n  connections: {}\n"
        self.assertEqual(module.automatic_network(config), config.replace("container:\n", 'container:\n  network: ""\n'))

    def test_duplicate_or_inline_container_refused(self):
        for config in ("container: {network: bridge}\n", "container:\n  network: bridge\n  network: host\n", "container:\ncontainer:\n"):
            with self.subTest(config=config), self.assertRaises(ValueError):
                module.automatic_network(config)

    def test_ci_postgres_uses_service_dns_and_network_guard_precedes_migrations(self):
        workflow = (ROOT / ".forgejo/workflows/quality.yml").read_text()
        self.assertIn("@postgres:5432/gramly_welcome_test", workflow)
        self.assertLess(workflow.index("Verify PostgreSQL service DNS"), workflow.index("alembic upgrade head"))


if __name__ == "__main__":
    unittest.main()
