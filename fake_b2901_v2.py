"""
FakeB2901v2 – Improved double-Langmuir-probe simulation backend.

Extends FakeB2901 with a physically more plausible *double_langmuir* model.

Model
-----
    I(V) = I_sat * tanh(V / W) * (1 + asymmetry * tanh(V / W))
            + g_sheath * V + i_offset + drift

where W = ``transition_width`` controls how many volts the central
transition spans (default ``2 * te_eV``).  For T_e = 3 eV the tanh
reaches 96 % saturation at V ≈ ±2W ≈ ±12 V, producing a smooth S-curve
instead of a step function.

The ``asymmetry`` term creates unequal saturation levels, ``i_offset``
adds a constant current bias, ``drift_per_point`` accumulates linear
drift, and ``noise_corr`` (0..1) introduces low-frequency wander.
"""
from __future__ import annotations

import math

from fake_b2901 import FakeB2901


class FakeB2901v2(FakeB2901):
    """FakeB2901 with an improved *double_langmuir* IV model."""

    IDN = "Keysight Technologies,B2901A,SIM00001,2.0.0 (SIM-v2)"

    _VALID_MODELS = ("tanh", "resistor", "double_langmuir", "single_probe")

    #: Per-model default sheath conductance (S).  The historic value
    #: 5e-5 is correct for the symmetric double-Langmuir form whose
    #: I_sat sits at ≈ 2 mA — at ±50 V the sheath term adds ±2.5 mA,
    #: a small slope on top of saturation.  The single-probe form
    #: has a Bohm-derived I_i_sat ≈ 5.6 µA, three orders of magnitude
    #: smaller, so the same sheath default would *swamp* the ion
    #: saturation plateau (sheath ≈ ±2.5 mA, ion sat ≈ ∓5.6 µA — a
    #: factor 450).  We therefore drop the single-probe default by
    #: ~500× so the negative plateau stays visually flat across the
    #: typical ±50 V sweep while still adding a hint of slope.
    #: Sized so the sheath term contributes at most ~10–15 % of the
    #: model's saturation current at the typical ±50 V sweep:
    #:   * single_probe : I_i,sat ≈ 5.6 µA → sheath ≤ 1·10⁻⁷ S
    #:                    (Plateau bleibt visuell flach im µA-Bereich)
    #:   * double_langmuir : I_sat = 2 mA → sheath = 5·10⁻⁶ S
    #:                    (±0.25 mA Slope on top of ±2 mA tanh —
    #:                    klares S mit leicht geneigten Plateaus.
    #:                    Der historische Wert 5·10⁻⁵ liess die Sheath
    #:                    mit ±2.5 mA die ±2 mA-Sättigung dominieren,
    #:                    sodass die Double-Kurve wie eine schräge
    #:                    Gerade durch Null aussah.)
    #:   * tanh / resistor : 5·10⁻⁵ historisch beibehalten — Modelle
    #:                    werden vom GUI-Pfad nicht aktiv genutzt.
    _DEFAULT_SHEATH_S: dict = {
        "single_probe":     1.0e-7,
        "double_langmuir":  5.0e-6,
        "tanh":             5.0e-5,
        "resistor":         5.0e-5,
    }

    def __init__(
        self,
        *,
        model: str = "double_langmuir",
        sheath_conductance: float | None = None,
        asymmetry: float = 0.0,
        i_offset: float = 0.0,
        drift_per_point: float = 0.0,
        noise_corr: float = 0.0,
        transition_width: float | None = None,
        # Single-probe parameters — used when ``model='single_probe'``.
        # Defaults model a 4 eV / weak Argon plasma; the
        # ion-saturation magnitude is derived from the textbook
        # Maxwellian flux balance unless the caller pins it.
        #
        # Maxwellian electron flux:  I_e_sat = (1/4) n e <v_th,e> A
        # Bohm ion flux at sheath:   I_i_sat = 0.6 · n e v_Bohm A
        # Ratio (T_e cancels):       I_e_sat / I_i_sat ≈ 0.665 · √(m_i/m_e)
        # → Argon (m_i = 39.948 amu): ratio ≈ 180.
        # → Hydrogen:    ≈ 28
        # → Xenon:       ≈ 330
        i_electron_sat: float = 1.0e-3,   # 1 mA, positive magnitude
        i_ion_sat: float | None = None,   # default: Bohm-derived from m_i
        ion_mass_amu: float = 39.948,     # Argon by default
        v_plasma_V: float = 0.0,          # plasma potential in volts
        # Electron-sheath expansion slope above V_p.  Real single
        # probes show a slowly rising electron-saturation arm because
        # the sheath edge expands with bias — textbook plots are
        # *not* perfectly flat at high positive V.  Modelled as a
        # small linear term in (V − V_p), gated by the Gompertz
        # electron factor so the slope only kicks in once we are in
        # electron saturation (above the knee) and stays absent on
        # the ion-saturation plateau.  2e-6 S adds ≈ 100 µA at
        # V_p + 50 V, i.e. ~10 % of I_e_sat — moderate, sättigungs­
        # artig, klar von einem exponentiellen Anstieg unterscheidbar.
        electron_sat_slope: float = 2.0e-6,
        **kw,
    ) -> None:
        if model not in self._VALID_MODELS:
            raise ValueError(
                f"Unknown model {model!r}, "
                f"use one of {self._VALID_MODELS}")
        # The base FakeB2901 only knows tanh + resistor, so route
        # the v2-specific models through tanh as a harmless parent
        # default.
        parent_model = model if model in ("tanh", "resistor") else "tanh"
        super().__init__(model=parent_model, **kw)
        self.model = model
        # Resolve the model-aware sheath default only when the caller
        # did not pin one explicitly — keeps every existing override
        # (incl. all test fixtures that set it to 0.0) untouched.
        if sheath_conductance is None:
            self.sheath_conductance = self._DEFAULT_SHEATH_S.get(
                model, 5.0e-5)
        else:
            self.sheath_conductance = float(sheath_conductance)
        self.asymmetry = asymmetry
        self.i_offset = i_offset
        self.drift_per_point = drift_per_point
        self.noise_corr = max(0.0, min(noise_corr, 0.999))
        # transition_width in volts; default = 2*T_e so the
        # central S-curve spans roughly ±2*W ≈ ±4*T_e
        self.transition_width = (transition_width
                                 if transition_width is not None
                                 else 2.0 * self.te_eV)
        # Single-probe parameters (only consulted in single_probe mode).
        # ``i_ion_sat`` defaults to the Maxwellian Bohm-derived value
        # so the curve is physically plausible without explicit tuning.
        self.ion_mass_amu = float(ion_mass_amu)
        self.i_electron_sat = float(i_electron_sat)
        if i_ion_sat is None:
            self.i_ion_sat = self.i_electron_sat / self._bohm_e_to_i_ratio(
                self.ion_mass_amu)
        else:
            self.i_ion_sat = float(i_ion_sat)
        self.v_plasma_V = float(v_plasma_V)
        self.electron_sat_slope = float(electron_sat_slope)
        self._noise_state = 0.0
        self._point_idx = 0

    # ── physics helpers ───────────────────────────────────────────

    @staticmethod
    def _bohm_e_to_i_ratio(ion_mass_amu: float) -> float:
        """Maxwellian electron-to-ion saturation-current ratio.

        Derived from the random-thermal flux for electrons and the
        Bohm flux for ions at the sheath edge:

            I_e_sat / I_i_sat ≈ (1/(4·0.6)) · √(8/π) · √(m_i / m_e)
                              ≈ 0.665 · √(m_i / m_e)

        The temperature dependence cancels in the ratio.  Reference
        values: Hydrogen ≈ 28, Argon ≈ 180, Xenon ≈ 330.
        """
        m_e_amu = 5.4858e-4
        return 0.665 * math.sqrt(max(ion_mass_amu, 1e-9) / m_e_amu)

    @property
    def v_float_V(self) -> float:
        """Floating potential — the bias at which the Maxwellian
        single-probe IV crosses zero current.

        From the balance  I_e_sat · exp((V_f − V_p)/T_e) = I_i_sat:

            V_f = V_p − T_e · ln(I_e_sat / I_i_sat).

        For a 4 eV Argon plasma with the Bohm-derived default ratio
        this lands at V_f ≈ V_p − 21 V, so the IV's zero crossing is
        clearly *not* at V = 0 V — exactly the asymmetry that
        distinguishes a single-probe sweep from a double-probe one.
        """
        if self.i_ion_sat <= 0 or self.i_electron_sat <= 0:
            return self.v_plasma_V
        return self.v_plasma_V - self.te_eV * math.log(
            self.i_electron_sat / self.i_ion_sat)

    # ── current simulation ────────────────────────────────────────

    def read_current(self) -> float:
        """Return simulated probe current for the present voltage."""
        self._maybe_fail("read_current")
        if not self._output_on:
            self._in_compliance = False
            return 0.0

        V = self._voltage

        if self.model == "double_langmuir":
            arg = V / self.transition_width
            arg = max(-50.0, min(50.0, arg))
            th = math.tanh(arg)
            # asymmetry: |I_sat+| = I_sat*(1+a), |I_sat-| = I_sat*(1-a)
            i = (self.i_sat * th * (1.0 + self.asymmetry * th)
                 + self.sheath_conductance * V
                 + self.i_offset
                 + self.drift_per_point * self._point_idx)
        elif self.model == "single_probe":
            # Smoothed textbook Langmuir-probe IV (Maxwellian electrons):
            #
            #   I(V) = -I_i_sat + I_e_sat · f((V − V_p)/T_e)
            #                   + g_sheath · V + offset + drift
            #
            # with f(x) = 1 − exp(−exp(x))  (Gompertz double-exponential).
            # This preserves the textbook exponential rise in the
            # retarding region (f(x) ≈ exp(x) for x ≪ 0) and a
            # sharp electron-saturation knee at V_p (f(0) ≈ 0.63,
            # f(+T_e) ≈ 0.93, f(+2 T_e) ≈ 0.999).  A plain sigmoid
            # would reach only 0.5 at V_p — the curve there would
            # look halfway-saturated instead of just past the knee.
            #
            # Zero crossing (V_f) is implicit:
            #   V_f = V_p − T_e · ln(I_e_sat / I_i_sat).
            # With Bohm-derived defaults this puts V_f ≈ V_p − 21 V
            # for Argon at T_e = 4 eV — clearly *not* at V = 0 V.
            arg = (V - self.v_plasma_V) / max(self.te_eV, 1e-3)
            arg = max(-50.0, min(50.0, arg))
            e_factor = 1.0 - math.exp(-math.exp(arg))
            # Electron-sheath expansion: linear in (V − V_p), but
            # weighted by the Gompertz electron factor so it vanishes
            # on the ion-saturation plateau and grows in to its full
            # slope only above the knee.
            e_sheath = (self.electron_sat_slope
                        * (V - self.v_plasma_V) * e_factor)
            i = (-self.i_ion_sat
                 + self.i_electron_sat * e_factor
                 + e_sheath
                 + self.sheath_conductance * V
                 + self.i_offset
                 + self.drift_per_point * self._point_idx)
        elif self.model == "resistor":
            i = V / self.resistance
        else:  # tanh
            arg = V / (2.0 * self.te_eV) * 11604.52
            arg = max(-50.0, min(50.0, arg))
            i = self.i_sat * math.tanh(arg)

        # noise: white + optional IIR-correlated wander
        if self.noise_std > 0:
            white = self._rng.gauss(0, self.noise_std)
            if self.noise_corr > 0:
                c = self.noise_corr
                self._noise_state = (c * self._noise_state
                                     + math.sqrt(1.0 - c * c) * white)
                i += self._noise_state
            else:
                i += white

        self._point_idx += 1
        self._in_compliance = abs(i) > self.current_compliance
        if self._in_compliance:
            i = math.copysign(self.current_compliance, i)
        return i
