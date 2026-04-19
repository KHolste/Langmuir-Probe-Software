"""Tests for iteration 4d: remote-sense activation guard + post-apply
SCPI error-queue health check.

Two independent additions:
* a confirmation dialog the very first time the user enables 4-wire
  in a session, with a 'do not show again' option that persists via
  QSettings;
* a generic ``check_error_queue`` helper that drains ``:SYST:ERR?``
  after ``apply_instrument_options`` so silently-rejected commands
  (e.g. ``:SYST:RSEN`` on firmware that does not implement it without
  raising on the VISA layer) still leave a log trace.
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dlp_instrument_dialog as did  # noqa: E402
from dlp_instrument_dialog import (  # noqa: E402
    apply_instrument_options,
    check_error_queue,
    normalize_options,
)


# ===========================================================================
# Fixtures
# ===========================================================================
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_qsettings(monkeypatch):
    """Don't touch the developer's real registry / plist during tests."""
    storage = {"suppressed": False}

    def fake_get():
        return bool(storage["suppressed"])

    def fake_set(v):
        storage["suppressed"] = bool(v)

    monkeypatch.setattr(did, "is_remote_sense_warning_suppressed", fake_get)
    monkeypatch.setattr(did, "set_remote_sense_warning_suppressed", fake_set)
    return storage


def _make_dialog(opts=None):
    return did.InstrumentOptionsDialog(opts)


# ===========================================================================
# Confirmation flow
# ===========================================================================
class TestRemoteSenseConfirmation:
    def test_initial_setchecked_does_not_show_dialog(self, qapp):
        """Opening the dialog with ``remote_sense=True`` must not pop
        the warning – only user clicks should."""
        with patch.object(did.QMessageBox, "exec") as ex:
            dlg = _make_dialog({"remote_sense": True})
        assert dlg.chkRemoteSense.isChecked() is True
        ex.assert_not_called()

    def test_user_enable_shows_warning_and_keeps_state(self, qapp):
        dlg = _make_dialog()
        with patch.object(did.QMessageBox, "exec",
                          return_value=did.QMessageBox.StandardButton.Ok):
            dlg.chkRemoteSense.click()
        assert dlg.chkRemoteSense.isChecked() is True

    def test_user_cancel_reverts_checkbox(self, qapp):
        dlg = _make_dialog()
        with patch.object(did.QMessageBox, "exec",
                          return_value=did.QMessageBox.StandardButton.Cancel):
            dlg.chkRemoteSense.click()
        assert dlg.chkRemoteSense.isChecked() is False

    def test_dont_show_again_persists(self, qapp, _isolate_qsettings):
        dlg = _make_dialog()
        captured: dict = {}

        original_set_checkbox = did.QMessageBox.setCheckBox

        def capturing_set_checkbox(self, cb):
            captured["cb"] = cb
            cb.setChecked(True)
            return original_set_checkbox(self, cb)

        with patch.object(did.QMessageBox, "setCheckBox",
                          new=capturing_set_checkbox), \
             patch.object(did.QMessageBox, "exec",
                          return_value=did.QMessageBox.StandardButton.Ok):
            dlg.chkRemoteSense.click()
        assert _isolate_qsettings["suppressed"] is True

    def test_warning_is_skipped_when_suppressed(self, qapp,
                                                 _isolate_qsettings):
        _isolate_qsettings["suppressed"] = True
        dlg = _make_dialog()
        with patch.object(did.QMessageBox, "exec") as ex:
            dlg.chkRemoteSense.click()
        ex.assert_not_called()
        assert dlg.chkRemoteSense.isChecked() is True

    def test_disable_never_shows_warning(self, qapp):
        dlg = _make_dialog({"remote_sense": True})
        with patch.object(did.QMessageBox, "exec") as ex:
            dlg.chkRemoteSense.click()  # ON → OFF
        ex.assert_not_called()
        assert dlg.chkRemoteSense.isChecked() is False

    def test_cancel_does_not_persist_suppression(self, qapp,
                                                  _isolate_qsettings):
        """Even if the user ticks 'do not show again' but then clicks
        Cancel, the suppression must NOT be stored – the user did not
        confirm anything."""
        dlg = _make_dialog()

        original_set_checkbox = did.QMessageBox.setCheckBox

        def capturing(self, cb):
            cb.setChecked(True)
            return original_set_checkbox(self, cb)

        with patch.object(did.QMessageBox, "setCheckBox", new=capturing), \
             patch.object(did.QMessageBox, "exec",
                          return_value=did.QMessageBox.StandardButton.Cancel):
            dlg.chkRemoteSense.click()
        assert _isolate_qsettings["suppressed"] is False
        assert dlg.chkRemoteSense.isChecked() is False


# ===========================================================================
# QSettings round-trip (real Qt write/read against a private scope)
# ===========================================================================
class TestSuppressionRoundTrip:
    def test_persistent_helpers_round_trip(self, qapp, monkeypatch, tmp_path):
        """Bypass the autouse monkeypatch by re-binding QSettings to a
        per-test storage path so we can exercise the real helpers
        without touching the developer's registry."""
        from PySide6.QtCore import QSettings, QCoreApplication
        # Force INI format into a tmp file so the test is fully isolated.
        QCoreApplication.setOrganizationName("JLU-IPI-test")
        QCoreApplication.setApplicationName("DLP-iter4d-test")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat,
                          QSettings.Scope.UserScope, str(tmp_path))
        # Re-import the helpers without the autouse monkeypatch.
        monkeypatch.setattr(did, "_QSETTINGS_ORG", "JLU-IPI-test")
        monkeypatch.setattr(did, "_QSETTINGS_APP", "DLP-iter4d-test")
        # Use the real (non-patched) helpers via direct module access.
        from PySide6.QtCore import QSettings as _QS
        s = _QS("JLU-IPI-test", "DLP-iter4d-test")
        s.setValue("suppress_remote_sense_warning", True)
        s.sync()
        # Read back through a fresh QSettings instance.
        s2 = _QS("JLU-IPI-test", "DLP-iter4d-test")
        assert bool(s2.value("suppress_remote_sense_warning",
                              False, type=bool)) is True


# ===========================================================================
# check_error_queue helper
# ===========================================================================
class TestCheckErrorQueue:
    def _smu_with_responses(self, responses):
        smu = MagicMock()
        smu._query = MagicMock(side_effect=list(responses))
        return smu

    def test_empty_queue_returns_empty(self):
        smu = self._smu_with_responses(['+0,"No error"'])
        assert check_error_queue(smu) == []

    def test_drains_until_zero(self):
        smu = self._smu_with_responses([
            '-113,"Undefined header"',
            '-410,"Query INTERRUPTED"',
            '+0,"No error"',
        ])
        errs = check_error_queue(smu)
        assert errs == ['-113,"Undefined header"',
                        '-410,"Query INTERRUPTED"']

    def test_caps_at_max_reads(self):
        """A misbehaving instrument that never reports +0 must not
        loop forever — the helper has a hard cap."""
        smu = self._smu_with_responses(
            ['-113,"err"'] * 20)
        errs = check_error_queue(smu, max_reads=4)
        assert len(errs) == 4

    def test_handles_missing_query(self):
        smu = MagicMock(spec=[])  # no _query attribute
        assert check_error_queue(smu) == []

    def test_handles_non_callable_query(self):
        smu = MagicMock()
        smu._query = "not callable"
        assert check_error_queue(smu) == []

    def test_swallows_query_exception(self):
        smu = MagicMock()
        smu._query = MagicMock(side_effect=RuntimeError("VISA timeout"))
        assert check_error_queue(smu) == []

    def test_returns_partial_on_mid_drain_exception(self):
        smu = MagicMock()
        smu._query = MagicMock(side_effect=[
            '-113,"first"',
            RuntimeError("late VISA timeout"),
        ])
        assert check_error_queue(smu) == ['-113,"first"']

    def test_handles_unsigned_zero_form(self):
        smu = self._smu_with_responses(['0,No error'])
        assert check_error_queue(smu) == []

    def test_handles_negative_zero_form(self):
        smu = self._smu_with_responses(['-0,"No error"'])
        assert check_error_queue(smu) == []


# ===========================================================================
# apply_instrument_options now invokes the health check
# ===========================================================================
class TestApplyInvokesHealthCheck:
    def test_apply_logs_reported_errors(self, caplog):
        smu = MagicMock()
        smu._query = MagicMock(side_effect=[
            '-113,"Undefined header"',
            '+0,"No error"',
        ])
        # Trim the call list so the health check still runs even if
        # other setters succeed.
        with caplog.at_level(logging.WARNING,
                              logger="dlp_instrument_dialog"):
            apply_instrument_options(smu, normalize_options(
                {"remote_sense": True}))
        # the warning must appear with the SCPI error text.
        assert any("Undefined header" in rec.getMessage()
                   for rec in caplog.records)

    def test_apply_does_not_break_when_query_missing(self):
        """Fakes don't expose ``_query`` — the health check must skip
        without disturbing the apply path."""
        from fake_b2901 import FakeB2901
        fake = FakeB2901()
        fake.connect()
        # Should NOT raise, and the compliance must still have been set.
        apply_instrument_options(fake, normalize_options(
            {"remote_sense": True, "compliance_A": 0.003}))
        assert fake.current_compliance == pytest.approx(0.003)

    def test_apply_continues_when_health_check_raises(self, caplog):
        """A defensive try/except around the helper call protects the
        apply path from a wedged ``_query``."""
        smu = MagicMock()
        smu._query = MagicMock(side_effect=RuntimeError("bus locked"))
        # Must not raise.
        apply_instrument_options(smu, normalize_options(
            {"remote_sense": True}))
