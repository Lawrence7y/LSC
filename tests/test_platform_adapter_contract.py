from __future__ import annotations

import inspect

import pytest

from lsc.platforms.capabilities import all_platform_capabilities
from lsc.platforms.registry import get_adapters


@pytest.mark.parametrize("adapter", get_adapters())
def test_builtin_adapter_exposes_v2_capabilities(adapter):
    assert isinstance(adapter.platform, str) and adapter.platform
    assert isinstance(adapter.display_name, str) and adapter.display_name
    assert callable(adapter.can_handle)
    assert callable(adapter.parse)
    assert callable(adapter.parse_with_context)
    capabilities = adapter.capabilities
    assert capabilities.platform == adapter.platform
    assert capabilities.platform_id == adapter.platform
    assert capabilities.preferred_protocols
    assert capabilities.resolve_timeout_sec > 0
    assert capabilities.probe_timeout_sec > 0
    assert capabilities.refresh_margin_sec >= 0
    assert capabilities.error_recovery
    assert isinstance(capabilities.connection_policy, str)


def test_capability_registry_covers_every_builtin_adapter():
    declared = all_platform_capabilities()
    assert {adapter.platform for adapter in get_adapters()} <= set(declared)


@pytest.mark.parametrize("adapter", get_adapters())
def test_adapter_parse_is_instance_method_without_runtime_side_effect_contract(adapter):
    signature = inspect.signature(adapter.parse)
    assert "url" in signature.parameters
    # The registry owns platform selection; adapters must not expose FFmpeg
    # lifecycle methods that could bypass the runtime supervisor.
    assert not hasattr(adapter, "start_recording")
    assert not hasattr(adapter, "start_preview")
