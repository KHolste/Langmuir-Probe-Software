"""Focused tests: Triple-probe n_e uncertainty propagation from m_i_rel_unc.

Mirrors the Single / Double ion-composition CI contract: when the
shared ``ion_composition_context`` supplies a non-zero
``mi_rel_unc``, Triple must expose a 95 %% CI on ``n_e`` using the
honest mass-only propagation ``σ_n/n = ½·σ_m/m`` (because
``n_e ∝ 1/√m_i``).  When mi_rel_unc is zero, the CI is
``unavailable`` — never a false-tight zero.

These tests pin down:
* The pure helper returns CI fields only when inputs allow it,
  and widens monotonically with ``mi_rel_unc``.
* ``analyze_sample`` surfaces the CI fields alongside Te / n_e.
* ``TripleSample.from_worker_dict`` carries the CI round-trip.
* The CSV writer records the homogeneous CI context in the header
  (``mi_rel_unc``, ``ne_ci_method``, ``ne_ci_note``).
* Old worker payloads without the new keys still parse cleanly
  (backward compatibility).
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dlp_triple_analysis import (
    DEFAULT_AREA_M2,
    DEFAULT_SPECIES,
    analyze_sample,
    compute_ne_ci_m3,
    compute_ne_m3,
    mi_from_species,
)
from dlp_triple_dataset import TripleDataset, TripleSample


MI_AR = mi_from_species(DEFAULT_SPECIES)


# ===========================================================================
# compute_ne_ci_m3 — pure helper
# ===========================================================================
class TestComputeNeCi:
    def test_zero_mi_rel_unc_is_unavailable(self):
        ci = compute_ne_ci_m3(1e17, 0.0)
        assert ci["ne_ci_method"] == "unavailable"
        assert ci["ne_ci_note"] == "fit_only"
        assert ci["ne_ci95_lo_m3"] is None
        assert ci["ne_ci95_hi_m3"] is None
        assert ci["ne_ci_m_i_rel_unc"] == 0.0

    def test_nan_mi_rel_unc_is_unavailable(self):
        ci = compute_ne_ci_m3(1e17, float("nan"))
        assert ci["ne_ci_method"] == "unavailable"
        assert ci["ne_ci95_lo_m3"] is None

    def test_negative_mi_rel_unc_clamped_to_zero(self):
        ci = compute_ne_ci_m3(1e17, -0.05)
        # Clamping to zero ⇒ CI is reported as unavailable.  The
        # recorded m_i_rel_unc is the clamped value, not the raw input.
        assert ci["ne_ci_method"] == "unavailable"
        assert ci["ne_ci_m_i_rel_unc"] == 0.0

    def test_positive_mi_rel_unc_yields_symmetric_ci(self):
        n_e = 1e17
        rel = 0.10
        ci = compute_ne_ci_m3(n_e, rel)
        assert ci["ne_ci_method"] == "covariance"
        assert ci["ne_ci_note"] == "ion_mix"
        # n_e ∝ 1/√m_i  ⇒  σ_n/n = ½·σ_m/m
        sigma_n = n_e * 0.5 * rel
        assert ci["ne_ci95_lo_m3"] == pytest.approx(n_e - 1.96 * sigma_n)
        assert ci["ne_ci95_hi_m3"] == pytest.approx(n_e + 1.96 * sigma_n)
        # Symmetric around n_e.
        hw_lo = n_e - ci["ne_ci95_lo_m3"]
        hw_hi = ci["ne_ci95_hi_m3"] - n_e
        assert hw_lo == pytest.approx(hw_hi)

    def test_ci_widens_monotonically_with_mi_rel_unc(self):
        n_e = 1e17
        prev = 0.0
        for rel in (0.01, 0.05, 0.10, 0.25):
            ci = compute_ne_ci_m3(n_e, rel)
            half = 0.5 * (ci["ne_ci95_hi_m3"] - ci["ne_ci95_lo_m3"])
            assert half > prev
            prev = half

    def test_nonfinite_ne_returns_unavailable(self):
        ci = compute_ne_ci_m3(float("nan"), 0.1)
        assert ci["ne_ci_method"] == "unavailable"
        assert ci["ne_ci95_lo_m3"] is None

    def test_zero_ne_returns_unavailable(self):
        ci = compute_ne_ci_m3(0.0, 0.1)
        assert ci["ne_ci_method"] == "unavailable"


# ===========================================================================
# analyze_sample wiring
# ===========================================================================
class TestAnalyzeSampleCi:
    def _plausible_inputs(self) -> dict:
        return dict(
            v_d12=10.0, v_d13=2.0, i_measure_a=-1.5e-3,
            area_m2=DEFAULT_AREA_M2, mi_kg=MI_AR,
        )

    def test_default_no_mi_rel_unc_yields_unavailable_ci(self):
        r = analyze_sample(**self._plausible_inputs())
        assert "Te_eV" in r and "n_e_m3" in r
        assert r["ne_ci_method"] == "unavailable"
        assert r["ne_ci_note"] == "fit_only"
        assert r["ne_ci95_lo_m3"] is None

    def test_mi_rel_unc_propagates_into_result(self):
        rel = 0.08
        r = analyze_sample(**self._plausible_inputs(), mi_rel_unc=rel)
        assert r["ne_ci_method"] == "covariance"
        assert r["ne_ci_note"] == "ion_mix"
        assert r["ne_ci95_lo_m3"] is not None
        assert r["ne_ci95_hi_m3"] is not None
        # Half-width matches the closed form for the configured n_e.
        half = 0.5 * (r["ne_ci95_hi_m3"] - r["ne_ci95_lo_m3"])
        expected = 1.96 * r["n_e_m3"] * 0.5 * rel
        assert half == pytest.approx(expected, rel=1e-9)

    def test_invalid_ne_does_not_fabricate_ci(self):
        # V_d12 ≤ 2·V_d13 is physically invalid; Te becomes NaN ⇒ n_e
        # returns 0.0 from the guarded helper — so no CI should be
        # quoted even with a non-zero mi_rel_unc.
        r = analyze_sample(v_d12=1.0, v_d13=2.0, i_measure_a=-1e-3,
                           area_m2=DEFAULT_AREA_M2, mi_kg=MI_AR,
                           mi_rel_unc=0.1)
        assert r["n_e_m3"] == 0.0
        assert r["ne_ci_method"] == "unavailable"


# ===========================================================================
# TripleSample round-trip
# ===========================================================================
class TestTripleSampleRoundTrip:
    def _worker_payload(self, **over) -> dict:
        base = {
            "t_rel_s": 0.25, "v_d12_setpoint": 25.0, "v_d12_actual": 25.02,
            "u_meas_v": 2.1, "v_d13": 2.1, "i_a": -1.4e-3,
            "Te_eV": 3.03, "n_e_m3": 1.2e17,
            "species": "Argon (Ar)", "area_m2": DEFAULT_AREA_M2,
            "mi_kg": MI_AR,
            "mi_rel_unc": 0.1,
            "ne_ci95_lo_m3": 1.0824e17,
            "ne_ci95_hi_m3": 1.3176e17,
            "ne_ci_method": "covariance",
            "ne_ci_note": "ion_mix",
        }
        base.update(over)
        return base

    def test_full_payload_roundtrip(self):
        s = TripleSample.from_worker_dict(self._worker_payload())
        assert s.mi_rel_unc == pytest.approx(0.1)
        assert s.ne_ci95_lo_m3 == pytest.approx(1.0824e17)
        assert s.ne_ci95_hi_m3 == pytest.approx(1.3176e17)
        assert s.ne_ci_method == "covariance"
        assert s.ne_ci_note == "ion_mix"

    def test_legacy_payload_without_ci_fields_parses_cleanly(self):
        # Old payloads have no CI keys at all — must not crash, and
        # the new optional fields must default to None (not 0.0).
        legacy = {
            "t_rel_s": 1.0, "v_d12_setpoint": 25.0, "v_d12_actual": 25.0,
            "u_meas_v": 2.0, "v_d13": 2.0, "i_a": -1e-3,
            "Te_eV": 2.9, "n_e_m3": 1e17,
            "species": "Argon (Ar)", "area_m2": DEFAULT_AREA_M2,
            "mi_kg": MI_AR,
        }
        s = TripleSample.from_worker_dict(legacy)
        assert s.mi_rel_unc is None
        assert s.ne_ci95_lo_m3 is None
        assert s.ne_ci95_hi_m3 is None
        assert s.ne_ci_method is None
        assert s.ne_ci_note is None

    def test_csv_row_still_has_exactly_eight_columns(self):
        # CI fields must NOT change the per-row CSV layout — that
        # would break any existing reader.
        s = TripleSample.from_worker_dict(self._worker_payload())
        parts = s.as_csv_row().split(",")
        assert len(parts) == 8


# ===========================================================================
# CSV header persistence
# ===========================================================================
class TestCsvHeaderPersistence:
    def _add_samples(self, ds: TripleDataset, *,
                    mi_rel_unc: float, method: str, note: str) -> None:
        for i in range(3):
            ds.add(TripleSample.from_worker_dict({
                "t_rel_s": 0.25 * i, "v_d12_setpoint": 25.0,
                "v_d12_actual": 25.0, "u_meas_v": 2.0, "v_d13": 2.0,
                "i_a": -1e-3, "Te_eV": 3.0, "n_e_m3": 1e17,
                "species": "Argon (Ar)", "area_m2": DEFAULT_AREA_M2,
                "mi_kg": MI_AR, "mi_rel_unc": mi_rel_unc,
                "ne_ci95_lo_m3": 9e16, "ne_ci95_hi_m3": 1.1e17,
                "ne_ci_method": method, "ne_ci_note": note,
            }))

    def test_homogeneous_ci_written_to_header(self, tmp_path):
        ds = TripleDataset()
        self._add_samples(ds, mi_rel_unc=0.1,
                          method="covariance", note="ion_mix")
        p = ds.write_csv(tmp_path / "triple.csv")
        text = p.read_text(encoding="utf-8")
        assert "# mi_rel_unc: 0.1" in text
        assert "# ne_ci_method: covariance" in text
        assert "# ne_ci_note: ion_mix" in text

    def test_zero_mi_rel_unc_writes_unavailable_method(self, tmp_path):
        ds = TripleDataset()
        self._add_samples(ds, mi_rel_unc=0.0,
                          method="unavailable", note="fit_only")
        p = ds.write_csv(tmp_path / "triple.csv")
        text = p.read_text(encoding="utf-8")
        assert "# mi_rel_unc: 0.0" in text
        assert "# ne_ci_method: unavailable" in text

    def test_heterogeneous_mi_rel_unc_is_not_written(self, tmp_path):
        # If a run somehow mixed different mi_rel_unc values (e.g.
        # context changed mid-run), the writer must NOT pick one —
        # the header slot is reserved for homogeneous facts only.
        ds = TripleDataset()
        self._add_samples(ds, mi_rel_unc=0.1,
                          method="covariance", note="ion_mix")
        # Override one sample's field to force heterogeneity.
        ds.samples[1].mi_rel_unc = 0.2
        p = ds.write_csv(tmp_path / "triple.csv")
        text = p.read_text(encoding="utf-8")
        assert "# mi_rel_unc:" not in text

    def test_old_dataset_without_ci_fields_writes_cleanly(self, tmp_path):
        # Legacy samples (no mi_rel_unc / ne_ci_* fields) must still
        # write a readable CSV — no blank header lines, no crashes.
        ds = TripleDataset()
        for i in range(2):
            ds.add(TripleSample(
                t_s=0.25 * i, u_supply_V=25.0, u_measure_V=2.0,
                i_measure_A=-1e-3, v_d12_V=25.0, v_d13_V=2.0,
                te_eV=3.0, ne_m3=1e17,
                species="Argon (Ar)", area_m2=DEFAULT_AREA_M2,
                mi_kg=MI_AR,
            ))
        p = ds.write_csv(tmp_path / "triple_legacy.csv")
        text = p.read_text(encoding="utf-8")
        # The scientific header rows must be present.
        assert "# species: Argon (Ar)" in text
        # But the CI slots must stay silent — no confusing blanks.
        assert "# mi_rel_unc:" not in text
        assert "# ne_ci_method:" not in text
