"""UI trigger for the legacy-data migration.

Covers:
  * the ``Migrate Legacy Data…`` button is wired into LPMainWindow
  * the click handler shows a friendly info dialog when there is
    nothing to migrate
  * the confirm dialog routes Cancel / Copy / Move correctly to
    :func:`paths.migrate_legacy_lp_data`
  * success and failure are surfaced via log + dialog
  * the startup hint is logged when legacy data is present and
    silent when it is not
  * regression: no auto-migration on app start
"""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_legacy(base_dir):
    """Plant a small legacy tree at ``<base_dir>/double_langmuir/``."""
    legacy = base_dir / "double_langmuir"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "DLP_2026-01-01T08-00-00.csv").write_text(
        "loose", encoding="utf-8")
    (legacy / "double").mkdir()
    (legacy / "double" / "LP_2026-01-02T09-00-00_double.csv").write_text(
        "nested", encoding="utf-8")
    return legacy


# ---------------------------------------------------------------------------
class TestMigrateButtonInstalled:
    def test_button_present_after_init(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            assert hasattr(win, "btnMigrateLegacy")
            btn = win.btnMigrateLegacy
            assert btn.text().startswith("Migrate Legacy")
            # Must live in the same parent layout as the Plot button
            # — i.e. plot-header row.  Validate by asking Qt for
            # a common parent widget.
            assert btn.parent() is win.btnPlotSettings.parent()
        finally:
            win.close()

    def test_button_click_is_connected(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            calls = {"open": False}
            with patch.object(win, "_open_migrate_legacy_dialog",
                              lambda: calls.update({"open": True})):
                win.btnMigrateLegacy.clicked.emit()
                assert calls["open"]
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestEmptyLegacyShowsInfoDialog:
    def test_empty_legacy_does_not_call_migrate(
            self, qapp, tmp_path, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        import paths as _paths
        # Point legacy + new at empty tmp dirs.
        monkeypatch.setattr(_paths, "legacy_lp_data_dir",
                            lambda: tmp_path / "double_langmuir")
        monkeypatch.setattr(_paths, "lp_measurements_data_dir",
                            lambda: tmp_path / "lp_measurements")
        win = LPMainWindow()
        try:
            shown_texts: list[str] = []  # text strings, not box refs
            monkeypatch.setattr(
                QMessageBox, "exec",
                lambda self_: shown_texts.append(self_.text())
                or QMessageBox.StandardButton.Ok)
            with patch("paths.migrate_legacy_lp_data") as mig:
                win._open_migrate_legacy_dialog()
                mig.assert_not_called()
            assert len(shown_texts) == 1
            assert "Nothing to migrate" in shown_texts[0]
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestConfirmFlowRoutesCorrectly:
    @pytest.fixture
    def env(self, qapp, tmp_path, monkeypatch):
        import paths as _paths
        legacy = _make_legacy(tmp_path)
        monkeypatch.setattr(_paths, "legacy_lp_data_dir", lambda: legacy)
        monkeypatch.setattr(_paths, "lp_measurements_data_dir",
                            lambda: tmp_path / "lp_measurements")
        return tmp_path

    def test_cancel_does_not_invoke_migrate(self, env, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        win = LPMainWindow()
        try:
            # Force the confirm dialog to "click Cancel".
            def _fake_exec(box):
                box.setProperty("__test_clicked",
                                box.button(
                                    QMessageBox.StandardButton.Cancel))
                # PySide returns int from exec(); the button is read
                # via clickedButton() which we monkeypatch below.
                return 0
            monkeypatch.setattr(QMessageBox, "exec", _fake_exec)
            monkeypatch.setattr(
                QMessageBox, "clickedButton",
                lambda self_: self_.property("__test_clicked"))
            with patch("paths.migrate_legacy_lp_data") as mig:
                win._open_migrate_legacy_dialog()
                mig.assert_not_called()
        finally:
            win.close()

    def test_copy_choice_calls_migrate_with_copy_true(
            self, env, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        win = LPMainWindow()
        try:
            calls = {"exec_count": 0}

            def _fake_exec(box):
                calls["exec_count"] += 1
                if calls["exec_count"] == 1:
                    # First exec is the confirm dialog → click Copy
                    # (the AcceptRole button is the second non-
                    # standard button; identify by text).
                    for b in box.buttons():
                        if b.text().startswith("Copy"):
                            box.setProperty("__clicked", b)
                            break
                else:
                    # Second exec is the done dialog → just OK.
                    box.setProperty("__clicked",
                                    box.button(
                                        QMessageBox.StandardButton.Ok))
                return 0
            monkeypatch.setattr(QMessageBox, "exec", _fake_exec)
            monkeypatch.setattr(
                QMessageBox, "clickedButton",
                lambda self_: self_.property("__clicked"))
            with patch("paths.migrate_legacy_lp_data",
                       return_value=2) as mig:
                win._open_migrate_legacy_dialog()
                mig.assert_called_once()
                assert mig.call_args.kwargs.get("copy") is True
        finally:
            win.close()

    def test_move_choice_calls_migrate_with_copy_false(
            self, env, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        win = LPMainWindow()
        try:
            calls = {"exec_count": 0}

            def _fake_exec(box):
                calls["exec_count"] += 1
                if calls["exec_count"] == 1:
                    for b in box.buttons():
                        if b.text().startswith("Move"):
                            box.setProperty("__clicked", b)
                            break
                else:
                    box.setProperty("__clicked",
                                    box.button(
                                        QMessageBox.StandardButton.Ok))
                return 0
            monkeypatch.setattr(QMessageBox, "exec", _fake_exec)
            monkeypatch.setattr(
                QMessageBox, "clickedButton",
                lambda self_: self_.property("__clicked"))
            with patch("paths.migrate_legacy_lp_data",
                       return_value=2) as mig:
                win._open_migrate_legacy_dialog()
                mig.assert_called_once()
                assert mig.call_args.kwargs.get("copy") is False
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestSuccessAndFailureFeedback:
    @pytest.fixture
    def env(self, qapp, tmp_path, monkeypatch):
        import paths as _paths
        legacy = _make_legacy(tmp_path)
        monkeypatch.setattr(_paths, "legacy_lp_data_dir", lambda: legacy)
        monkeypatch.setattr(_paths, "lp_measurements_data_dir",
                            lambda: tmp_path / "lp_measurements")
        return tmp_path

    def test_success_logs_and_invokes_done_indirection(
            self, env, monkeypatch):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            done_calls: list[tuple[str, int]] = []
            monkeypatch.setattr(
                win, "_show_migration_done",
                lambda mode, n: done_calls.append((mode, n)))
            with patch("paths.migrate_legacy_lp_data",
                       return_value=2):
                win._run_legacy_migration(copy_mode=True)
            assert done_calls == [("copy", 2)]
            text = win.txtLog.toPlainText()
            assert "2 item" in text and "copy" in text
        finally:
            win.close()

    def test_failure_logs_and_invokes_error_indirection(
            self, env, monkeypatch):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            err_calls: list[tuple[str, str]] = []
            monkeypatch.setattr(
                win, "_show_migration_error",
                lambda mode, msg: err_calls.append((mode, msg)))
            with patch("paths.migrate_legacy_lp_data",
                       side_effect=OSError("disk on fire")):
                win._run_legacy_migration(copy_mode=False)
            assert err_calls == [("move", "disk on fire")]
            text = win.txtLog.toPlainText()
            assert "failed" in text.lower()
            assert "disk on fire" in text
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestStartupHint:
    def test_legacy_present_emits_log_hint(self, qapp, tmp_path,
                                            monkeypatch):
        # Plant legacy under tmp_path, redirect helpers there.
        import paths as _paths
        legacy = _make_legacy(tmp_path)
        monkeypatch.setattr(_paths, "legacy_lp_data_dir", lambda: legacy)
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            # Re-run the announcer with the patched legacy path
            # (the original ran during __init__ before the patch).
            win.txtLog.clear()
            win._announce_legacy_data_if_present()
            text = win.txtLog.toPlainText()
            assert "Legacy data folder detected" in text
            assert "2 item" in text
        finally:
            win.close()

    def test_no_legacy_no_hint(self, qapp, tmp_path, monkeypatch):
        import paths as _paths
        # Empty / missing legacy folder.
        monkeypatch.setattr(_paths, "legacy_lp_data_dir",
                            lambda: tmp_path / "does_not_exist")
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.txtLog.clear()
            win._announce_legacy_data_if_present()
            text = win.txtLog.toPlainText()
            assert "Legacy data folder detected" not in text
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestNoAutoMigrationOnStart:
    def test_init_does_not_call_migrate_helper(self, qapp, tmp_path,
                                                  monkeypatch):
        import paths as _paths
        _make_legacy(tmp_path)
        monkeypatch.setattr(_paths, "legacy_lp_data_dir",
                            lambda: tmp_path / "double_langmuir")
        monkeypatch.setattr(_paths, "lp_measurements_data_dir",
                            lambda: tmp_path / "lp_measurements")
        with patch("paths.migrate_legacy_lp_data") as mig:
            from LPmeasurement import LPMainWindow
            win = LPMainWindow()
            try:
                mig.assert_not_called()
            finally:
                win.close()
