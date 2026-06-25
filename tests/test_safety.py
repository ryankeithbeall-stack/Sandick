"""Tests for the ALLOW_LIVE_TX broadcast kill-switch (safety.py)."""

import pytest

from sandick.safety import LiveTxNotAllowed, require_tx_allowed, tx_allowed


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_TX", raising=False)
    assert tx_allowed() is False
    with pytest.raises(LiveTxNotAllowed, match="ALLOW_LIVE_TX"):
        require_tx_allowed("ctx")


def test_only_exact_one_enables(monkeypatch):
    for val in ("0", "true", "yes", "", "2"):
        monkeypatch.setenv("ALLOW_LIVE_TX", val)
        assert tx_allowed() is False
        with pytest.raises(LiveTxNotAllowed):
            require_tx_allowed()


def test_enabled_when_set_to_one(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TX", "1")
    assert tx_allowed() is True
    require_tx_allowed("ctx")  # does not raise


def test_context_in_message(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_TX", raising=False)
    with pytest.raises(LiveTxNotAllowed, match="keeper bridge"):
        require_tx_allowed("keeper bridge/submit")
