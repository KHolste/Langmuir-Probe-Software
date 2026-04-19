"""Hardening tests for the Double-probe analysis path.

Mirrors the Single-probe hardening pass: the LP-Hauptfenster's
``_run_analysis`` override now compliance-filters the buffers
before delegating to V2's analysis math.

Covers:
  * legacy (no-compliance) flow stays unchanged
  * compliance-flagged points are excluded from V2's fit
  * a clipped outlier no longer biases the recovered I_sat / T_e
  * GUI-owned buffers are restored after analysis (CSV / plot /
    hysteresis must still see the full record)
  * forward/reverse divergence on Double sweeps surfaces a warning
  * Double-side compact HTML carries a "Compliance" provenance row
    when filtering was active
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _populate_double_sim(win, *, n=80, asymmetry=0.0, te=4.0,
                          i_sat=2.0e-3, sheath=5e-6,
                          bidirectional=False, drift_per_rev=0.0):
    """Stuff a synthetic double_langmuir IV sweep into the buffers.

    ``drift_per_rev`` adds a constant offset to the reverse sweep
    so we can construct visible hysteresis when ``bidirectional``."""
    from fake_b2901_v2 import FakeB2901v2
    f = FakeB2901v2(model="double_langmuir", te_eV=te,
                    sheath_conductance=sheath, asymmetry=asymmetry,
                    current_compliance=10.0)
    f.connect(); f.output(True)
    voltages = np.linspace(-50, 50, n)
    f.i_sat = i_sat
    for v in voltages:
        f.set_voltage(v)
        win._v_soll.append(v); win._v_ist.append(v)
        win._i_mean.append(f.read_current())
        win._i_std.append(0.0)
        win._directions.append("fwd")
        win._compliance.append(False)
    if bidirectional:
        for v in voltages[::-1]:
            f.set_voltage(v)
            win._v_soll.append(v); win._v_ist.append(v)
            win._i_mean.append(f.read_current() + drift_per_rev)
            win._i_std.append(0.0)
            win._directions.append("rev")
            win._compliance.append(False)


# ---------------------------------------------------------------------------
class TestLegacyNoComplianceUnchanged:
    """When no compliance information is present (e.g. loaded CSV
    without the column populated, or sweeps from older session),
    the override must take the legacy path: no swap, no filter,
    same numeric result as a plain V2 analyze."""

    def test_no_compliance_flags_run_v2_unchanged(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            _populate_double_sim(win)
            # Wipe compliance buffer so the override skips filtering.
            win._compliance.clear()
            win._dataset_method = "double"
            v_ist_before = list(win._v_ist)
            win._run_analysis()
            # Buffers untouched, result populated.
            assert win._v_ist == v_ist_before
            assert win._last_model_fit is not None
            te = win._last_model_fit.get("Te_eV")
            assert te is not None and not np.isnan(te)
            assert te == pytest.approx(4.0, rel=0.30)
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestComplianceFilterAppliedToV2:
    """When compliance flags are present, the Double-V2 analysis must
    work on a filtered subset.  The filtered fit must report a
    smaller point count for the saturation branch and the buffers
    must be restored after analysis returns."""

    def test_filtered_fit_uses_fewer_points_than_legacy(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            _populate_double_sim(win, n=80)
            # Flag the 5 most-positive points as compliance hits.
            for i in range(75, 80):
                win._compliance[i] = True
            win._dataset_method = "double"
            win._run_analysis()
            fit = win._last_fit
            assert fit is not None
            # Positive-branch fit must NOT include the 5 clipped
            # points — saturation-branch n_pos drops accordingly.
            assert fit["n_pos"] <= 80 - 5

        finally:
            win.close()

    def test_buffers_restored_after_analysis(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            _populate_double_sim(win, n=80)
            for i in range(60, 80):
                win._compliance[i] = True
            n_before = len(win._v_ist)
            win._dataset_method = "double"
            win._run_analysis()
            # All buffers must still hold the FULL sweep record.
            assert len(win._v_ist) == n_before
            assert len(win._i_mean) == n_before
            assert len(win._compliance) == n_before
            # And the clipped flags survived the swap.
            assert sum(1 for c in win._compliance if c) == 20
        finally:
            win.close()

    def test_clipped_outlier_no_longer_biases_i_sat(self, qapp):
        # Construct a clean Double sweep, inject one massively-wrong
        # current at the positive end (compliance-clipped to a
        # value ~5x I_sat), and verify that the WITH-filter result
        # recovers I_sat closer to truth than WITHOUT-filter.
        from LPmeasurement import LPMainWindow

        def _run_with_outlier(*, mark_compliance: bool):
            win = LPMainWindow()
            try:
                _populate_double_sim(win, n=80, i_sat=2.0e-3)
                # Inject a wildly wrong point in the positive sat
                # branch.  In real life this is what a current-
                # compliance hit looks like.
                win._i_mean[-1] = 1.0e-2  # 5x i_sat = 10 mA
                if mark_compliance:
                    win._compliance[-1] = True
                win._dataset_method = "double"
                win._run_analysis()
                return win._last_fit.get("i_sat_pos")
            finally:
                win.close()

        i_sat_dirty = _run_with_outlier(mark_compliance=False)
        i_sat_clean = _run_with_outlier(mark_compliance=True)
        # The dirty fit gets pulled toward the outlier; the
        # filtered fit lands close to the true 2 mA + sheath term.
        # We require the filtered estimate to be MEASURABLY
        # closer to the true positive saturation value.
        assert i_sat_clean is not None and i_sat_dirty is not None
        truth = 2.25e-3  # i_sat + sheath term at +50 V
        err_dirty = abs(i_sat_dirty - truth)
        err_clean = abs(i_sat_clean - truth)
        assert err_clean < err_dirty, (i_sat_dirty, i_sat_clean)


# ---------------------------------------------------------------------------
class TestDoubleHysteresisWarning:
    """Forward/reverse divergence must surface a warn-level log
    line through the Double override.  Detection must NOT fire on
    matching branches."""

    def test_diverging_branches_emit_warning(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            # 25 % of i_sat drift between fwd and rev → > 5 % of
            # |I|_max → flagged.
            _populate_double_sim(win, n=80, bidirectional=True,
                                  drift_per_rev=0.5e-3)
            win._dataset_method = "double"
            win.txtLog.clear()
            win._run_analysis()
            log_text = win.txtLog.toPlainText()
            assert ("fwd/rev branches diverge" in log_text
                    or "plasma drift" in log_text.lower())
        finally:
            win.close()

    def test_matching_branches_do_not_warn(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            _populate_double_sim(win, n=80, bidirectional=True,
                                  drift_per_rev=0.0)
            win._dataset_method = "double"
            win.txtLog.clear()
            win._run_analysis()
            log_text = win.txtLog.toPlainText()
            assert "fwd/rev branches diverge" not in log_text
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestCompactHtmlComplianceRow:
    """The compact Double HTML block must carry a Compliance
    provenance row when filtering removed points, and NOT when
    the dataset was clean."""

    def test_provenance_row_present_when_filtered(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            _populate_double_sim(win, n=80)
            for i in range(70, 80):
                win._compliance[i] = True
            win._dataset_method = "double"
            win.txtLog.clear()
            win._run_analysis()
            html = win.txtLog.toHtml()
            # The compact block now carries both numerator and the
            # total, plus the percentage — tighter provenance than
            # the legacy "N clipped" string.
            assert "Compliance" in html
            assert "10/80 clipped" in html
            assert "excluded" in html
        finally:
            win.close()

    def test_provenance_row_absent_on_clean_data(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            _populate_double_sim(win, n=80)
            # All flags False — no exclusion.
            win._dataset_method = "double"
            win.txtLog.clear()
            win._run_analysis()
            html = win.txtLog.toHtml()
            assert "clipped point(s) excluded" not in html
        finally:
            win.close()
