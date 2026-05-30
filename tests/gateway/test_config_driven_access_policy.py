"""Tests for config-driven platform access policies at the gateway layer.

WeCom, Weixin, Yuanbao, and QQBot expose adapter-side policy knobs such as
``dm_policy`` / ``group_policy`` / ``allow_from`` / ``group_allow_from``. Those
knobs run before the shared gateway authorization check.

The gateway must not treat the mere presence of an adapter-owned policy surface
as authorization. Open policy is still unauthenticated network input. Only an
explicit allowlist match from the adapter-side policy may satisfy the gateway's
default-deny requirement when no env allowlist is configured.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.session import SessionSource


# Platforms whose adapters own their access policy at intake.
_OWN_POLICY_PLATFORMS = [
    Platform.WECOM,
    Platform.WEIXIN,
    Platform.YUANBAO,
    Platform.QQBOT,
]


def _clear_auth_env(monkeypatch) -> None:
    for key in (
        "WECOM_ALLOWED_USERS",
        "WEIXIN_ALLOWED_USERS",
        "YUANBAO_ALLOWED_USERS",
        "QQ_ALLOWED_USERS",
        "QQ_GROUP_ALLOWED_USERS",
        "TELEGRAM_ALLOWED_USERS",
        "GATEWAY_ALLOWED_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
        "WECOM_ALLOW_ALL_USERS",
        "WEIXIN_ALLOW_ALL_USERS",
        "YUANBAO_ALLOW_ALL_USERS",
        "QQ_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_runner(
    platform: Platform,
    config: GatewayConfig,
    *,
    enforces: bool,
    authorizes=None,
):
    """Build a bare GatewayRunner with one adapter for *platform*.

    ``enforces`` controls whether the adapter declares
    ``enforces_own_access_policy`` — i.e. whether it owns its access gate.
    """
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = config
    adapter = SimpleNamespace(send=AsyncMock(), enforces_own_access_policy=enforces)
    if authorizes is not None:
        adapter.authorizes_source_via_own_access_policy = authorizes
    runner.adapters = {platform: adapter}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    runner.pairing_store._is_rate_limited.return_value = False
    return runner, adapter


def _source(platform: Platform, *, chat_type: str = "dm") -> SessionSource:
    return SessionSource(
        platform=platform,
        user_id="some-user",
        chat_id="some-chat",
        user_name="tester",
        chat_type=chat_type,
    )


# ---------------------------------------------------------------------------
# Layer 1: the base-class contract and per-adapter overrides
# ---------------------------------------------------------------------------


def test_base_adapter_defaults_to_not_owning_access_policy():
    """Adapters that don't override the property delegate to the gateway."""
    from gateway.platforms.base import BasePlatformAdapter

    # The default lives on the base property descriptor.
    adapter = object()
    assert BasePlatformAdapter.enforces_own_access_policy.fget(adapter) is False
    assert BasePlatformAdapter.authorizes_source_via_own_access_policy(
        adapter,
        _source(Platform.TELEGRAM),
    ) is False


@pytest.mark.parametrize(
    "module_path, class_name",
    [
        ("gateway.platforms.wecom", "WeComAdapter"),
        ("gateway.platforms.weixin", "WeixinAdapter"),
        ("gateway.platforms.yuanbao", "YuanbaoAdapter"),
        ("gateway.platforms.qqbot.adapter", "QQAdapter"),
    ],
)
def test_own_policy_adapters_declare_the_flag(module_path, class_name):
    """The four config-policy adapters override the flag to True."""
    import importlib

    module = importlib.import_module(module_path)
    adapter_cls = getattr(module, class_name)
    # Property is overridden on the subclass and returns True regardless of
    # instance state (it reflects a static capability, not runtime config).
    value = adapter_cls.enforces_own_access_policy.fget(object.__new__(adapter_cls))
    assert value is True


# ---------------------------------------------------------------------------
# Layer 2: gateway requires an explicit adapter-side allowlist decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", _OWN_POLICY_PLATFORMS)
def test_open_own_policy_platform_default_denies_without_env_allowlist(monkeypatch, platform):
    """Open adapter policy is not authorization for network input."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={platform: PlatformConfig(enabled=True, extra={"dm_policy": "open"})}
    )
    runner, _adapter = _make_runner(platform, config, enforces=True)

    assert runner._is_user_authorized(_source(platform)) is False


@pytest.mark.parametrize("platform", _OWN_POLICY_PLATFORMS)
def test_open_own_policy_group_default_denies_without_env_allowlist(monkeypatch, platform):
    """Open group policy must not bypass the shared default-deny gate."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={platform: PlatformConfig(enabled=True, extra={"group_policy": "open"})}
    )
    runner, _adapter = _make_runner(platform, config, enforces=True)

    assert runner._is_user_authorized(_source(platform, chat_type="group")) is False


def test_adapter_explicit_policy_authorizes_matching_source(monkeypatch):
    """The gateway accepts a positive adapter-side allowlist decision."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.WECOM: PlatformConfig(enabled=True, extra={"dm_policy": "allowlist"})}
    )
    runner, _adapter = _make_runner(
        Platform.WECOM,
        config,
        enforces=True,
        authorizes=lambda source: source.user_id == "some-user",
    )

    assert runner._is_user_authorized(_source(Platform.WECOM)) is True


def test_adapter_empty_policy_decision_default_denies(monkeypatch):
    """An empty adapter-side allowlist is a denial, not an open fallback."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.WECOM: PlatformConfig(enabled=True, extra={"dm_policy": "allowlist"})}
    )
    runner, _adapter = _make_runner(
        Platform.WECOM,
        config,
        enforces=True,
        authorizes=lambda _source: False,
    )

    assert runner._is_user_authorized(_source(Platform.WECOM)) is False


@pytest.mark.parametrize(
    "module_path, class_name, platform, attrs",
    [
        (
            "gateway.platforms.wecom",
            "WeComAdapter",
            Platform.WECOM,
            {
                "_dm_policy": "allowlist",
                "_allow_from": ["some-user"],
                "_group_policy": "allowlist",
                "_group_allow_from": ["some-chat"],
                "_groups": {},
            },
        ),
        (
            "gateway.platforms.weixin",
            "WeixinAdapter",
            Platform.WEIXIN,
            {
                "_dm_policy": "allowlist",
                "_allow_from": ["some-user"],
                "_group_policy": "allowlist",
                "_group_allow_from": ["some-chat"],
            },
        ),
        (
            "gateway.platforms.qqbot.adapter",
            "QQAdapter",
            Platform.QQBOT,
            {
                "_dm_policy": "allowlist",
                "_allow_from": ["some-user"],
                "_group_policy": "allowlist",
                "_group_allow_from": ["some-chat"],
            },
        ),
    ],
)
def test_own_policy_adapters_authorize_only_explicit_allowlist_matches(
    module_path,
    class_name,
    platform,
    attrs,
):
    """Adapter-side auth hooks distinguish explicit allowlists from open mode."""
    import importlib

    module = importlib.import_module(module_path)
    adapter_cls = getattr(module, class_name)
    adapter = object.__new__(adapter_cls)
    for name, value in attrs.items():
        setattr(adapter, name, value)

    assert adapter.authorizes_source_via_own_access_policy(_source(platform)) is True
    assert adapter.authorizes_source_via_own_access_policy(
        SessionSource(platform=platform, user_id="other", chat_id="some-chat", chat_type="dm")
    ) is False

    setattr(adapter, "_dm_policy", "open")
    assert adapter.authorizes_source_via_own_access_policy(_source(platform)) is False


def test_yuanbao_policy_authorizes_only_explicit_allowlist_matches():
    from gateway.platforms.yuanbao import AccessPolicy, YuanbaoAdapter

    adapter = object.__new__(YuanbaoAdapter)
    adapter._access_policy = AccessPolicy(
        dm_policy="allowlist",
        dm_allow_from=["some-user"],
        group_policy="allowlist",
        group_allow_from=["some-chat"],
    )

    assert adapter.authorizes_source_via_own_access_policy(_source(Platform.YUANBAO)) is True
    assert adapter.authorizes_source_via_own_access_policy(
        SessionSource(platform=Platform.YUANBAO, user_id="other", chat_id="some-chat", chat_type="dm")
    ) is False

    adapter._access_policy = AccessPolicy(
        dm_policy="open",
        dm_allow_from=[],
        group_policy="open",
        group_allow_from=[],
    )
    assert adapter.authorizes_source_via_own_access_policy(_source(Platform.YUANBAO)) is False


def test_non_owning_platform_still_default_denies(monkeypatch):
    """Adapters that don't own their policy keep the env-only default-deny."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="t")}
    )
    runner, _adapter = _make_runner(Platform.TELEGRAM, config, enforces=False)

    assert runner._is_user_authorized(_source(Platform.TELEGRAM)) is False


def test_env_allowlist_still_takes_precedence_for_own_policy_platform(monkeypatch):
    """When an env allowlist IS set, it governs — adapter trust is a fallback.

    The adapter-trust branch only fires when no env allowlist exists, so an
    operator who sets ``WECOM_ALLOWED_USERS`` still gets env-based gating and
    a non-listed user is denied.
    """
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("WECOM_ALLOWED_USERS", "allowed-user")
    config = GatewayConfig(
        platforms={Platform.WECOM: PlatformConfig(enabled=True, extra={"dm_policy": "open"})}
    )
    runner, _adapter = _make_runner(Platform.WECOM, config, enforces=True)

    listed = SessionSource(
        platform=Platform.WECOM, user_id="allowed-user", chat_id="c",
        user_name="t", chat_type="dm",
    )
    stranger = SessionSource(
        platform=Platform.WECOM, user_id="stranger", chat_id="c",
        user_name="t", chat_type="dm",
    )
    assert runner._is_user_authorized(listed) is True
    assert runner._is_user_authorized(stranger) is False


def test_unknown_adapter_does_not_crash_trust_check(monkeypatch):
    """No adapter registered for the platform → safe default-deny."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(platforms={Platform.WECOM: PlatformConfig(enabled=True)})
    runner, _adapter = _make_runner(Platform.WECOM, config, enforces=True)
    runner.adapters = {}  # nothing registered

    assert runner._adapter_enforces_own_access_policy(Platform.WECOM) is False
    assert runner._is_user_authorized(_source(Platform.WECOM)) is False


# ---------------------------------------------------------------------------
# Layer 3: unauthorized-DM behavior reads config dm_policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dm_policy, expected",
    [
        ("allowlist", "ignore"),
        ("disabled", "ignore"),
        ("pairing", "pair"),
    ],
)
def test_unauthorized_dm_behavior_follows_config_dm_policy(monkeypatch, dm_policy, expected):
    """A restrictive dm_policy drops unauthorized DMs; pairing opts back in."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.WECOM: PlatformConfig(enabled=True, extra={"dm_policy": dm_policy})}
    )
    runner, _adapter = _make_runner(Platform.WECOM, config, enforces=True)

    assert runner._get_unauthorized_dm_behavior(Platform.WECOM) == expected


def test_unauthorized_dm_behavior_open_policy_keeps_default(monkeypatch):
    """``dm_policy: open`` is not restrictive → falls through to the default."""
    _clear_auth_env(monkeypatch)
    config = GatewayConfig(
        platforms={Platform.WECOM: PlatformConfig(enabled=True, extra={"dm_policy": "open"})}
    )
    runner, _adapter = _make_runner(Platform.WECOM, config, enforces=True)

    # No allowlist + no restrictive policy → open-gateway pairing default.
    assert runner._get_unauthorized_dm_behavior(Platform.WECOM) == "pair"
