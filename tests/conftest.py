"""Keep unit tests independent from developer-local service configuration."""

import pytest


@pytest.fixture(autouse=True)
def isolate_optional_remote_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use local adapters unless a test explicitly injects a remote client."""

    monkeypatch.setenv("MCP_TOOLS_URL", "")
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "")
    monkeypatch.setenv("RERANKER_URL", "")
