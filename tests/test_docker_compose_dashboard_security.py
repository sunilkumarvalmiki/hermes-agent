"""Static regressions for Docker Compose dashboard exposure."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOWS_COMPOSE = REPO_ROOT / "docker-compose.windows.yml"


def _windows_dashboard_service() -> dict:
    data = yaml.safe_load(WINDOWS_COMPOSE.read_text(encoding="utf-8"))
    return data["services"]["dashboard"]


def test_windows_compose_publishes_dashboard_only_on_host_loopback() -> None:
    service = _windows_dashboard_service()

    assert service["ports"] == ["127.0.0.1:9119:9119"]


def test_windows_compose_keeps_dashboard_host_gate_enabled() -> None:
    service = _windows_dashboard_service()
    command = service["command"]

    assert "--insecure" not in command
    assert "--host" in command
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert "--host-header-host" in command
    assert command[command.index("--host-header-host") + 1] == "127.0.0.1"
    assert "HERMES_DASHBOARD_HOST=0.0.0.0" not in service.get("environment", [])
