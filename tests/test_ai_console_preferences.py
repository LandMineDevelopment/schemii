from types import SimpleNamespace
from unittest.mock import Mock

from schemii.common.admin_config import AdminConfig
from schemii.schemii.ai.repository import InMemoryAiRepository
from schemii.schemii.ai.service import AiService


def test_ai_query_uses_current_owner_console_preferences():
    completed = SimpleNamespace(status="succeeded", results=[object()])
    console = SimpleNamespace(
        settings=Mock(return_value=SimpleNamespace(revision=7)),
        reserve=Mock(return_value=SimpleNamespace(id="execution")),
        run=Mock(),
        get=Mock(return_value=completed),
    )
    service = AiService(InMemoryAiRepository(), None, SimpleNamespace(
        admin_config=AdminConfig(), console=console,
    ))
    assert service._run_query("owner", "ws_" + "a" * 32, 3, "SELECT 1") is completed
    console.settings.assert_called_once_with("owner")
    owner, workspace, request = console.reserve.call_args.args
    assert owner == "owner"
    assert request.expected_settings_revision == 7
    assert request.mode == "managed_read"
    console.run.assert_called_once_with("owner", "execution")
