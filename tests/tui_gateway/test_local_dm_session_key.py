"""Create-time stamping for locally-served Desktop turns (#139818).

Regression: turns served by ``hermes serve`` / the TUI gateway persisted their session row through
``AIAgent._ensure_db_session`` with ``session_key=agent._gateway_session_key``. The TUI ``_make_agent``
factory never set that key, so remotely-authenticated (served basic-login) turns were born as
``source='desktop'`` rows with a NULL ``session_key``; the host's own local Desktop then failed to open
them (``4001 not_found``). ``_local_dm_session_key`` stamps the canonical local Bot Chat key at create time.
"""

from __future__ import annotations

import tui_gateway.agent_factory as af


def _stub_local_key(monkeypatch, profile: str | None = None):
    monkeypatch.setattr(af, "_resolve_agent_platform", lambda override: "desktop")

    def _profile_for_home(home):
        return profile

    import hermes_constants
    monkeypatch.setattr(hermes_constants, "profile_name_for_home", _profile_for_home, raising=False)


def test_desktop_turn_stamps_canonical_default_profile_key(monkeypatch):
    _stub_local_key(monkeypatch, profile="default")
    key = af._local_dm_session_key("20261025_011622_1b5f6c", {"profile_home": None})
    assert key == "agent:main:local:dm:20261025_011622_1b5f6c"


def test_desktop_turn_uses_session_profile(monkeypatch):
    _stub_local_key(monkeypatch, profile="growth")
    key = af._local_dm_session_key("20261025_011622_1b5f6c", {})
    assert key == "agent:growth:local:dm:20261025_011622_1b5f6c"


def test_non_local_surface_stamps_nothing(monkeypatch):
    monkeypatch.setattr(af, "_resolve_agent_platform", lambda override: "api")
    assert af._local_dm_session_key("20261025_011622_1b5f6c", {}) is None


def test_empty_session_id_stamps_nothing():
    assert af._local_dm_session_key("", {}) is None
    assert af._local_dm_session_key(None, {}) is None


def test_tui_surface_also_stamped(monkeypatch):
    monkeypatch.setattr(af, "_resolve_agent_platform", lambda override: "tui")
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "profile_name_for_home", lambda home: "default", raising=False)
    assert af._local_dm_session_key("abc", {}) == "agent:main:local:dm:abc"
