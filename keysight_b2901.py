"""
Keysight B2901 Source/Measure Unit – reusable PyVISA/SCPI driver.

Exposes the same PSU-like interface used by the Bühler RPA project
(connect, close, idn, set_voltage, read_voltage, read_current, output)
so it can be used as a drop-in alternative to FugPSU / CPXPSU.

Standalone – no Qt or GUI dependency.
"""
from __future__ import annotations
import time
from typing import Optional
import pyvisa


class KeysightB2901PSU:
    """Driver for the Keysight B2901A/B SMU over VISA (GPIB, USB, LAN)."""

    # NPLC presets: human-readable labels → NPLC values
    # (from Keysight B2900 programming guide, ref: agilent_B29xx speed_types)
    SPEED_PRESETS: dict[str, float] = {
        "Very fast (0.01)": 0.01,
        "Fast (0.1)": 0.1,
        "Medium (1)": 1.0,
        "Slow (10)": 10.0,
        "Very slow (100)": 100.0,
    }

    def __init__(
        self,
        *,
        visa_resource: str = "GPIB0::23::INSTR",
        timeout: float = 2.0,
        v_min: float = -250.0,
        v_max: float = 250.0,
        i_max: float = 0.1,
        current_compliance: float | None = None,
    ) -> None:
        self.visa_resource = visa_resource
        self.timeout_ms = int(timeout * 1000)
        self.v_min = v_min
        self.v_max = v_max
        self.i_max = i_max
        self.current_compliance = current_compliance if current_compliance is not None else i_max
        self.read_only = False
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst: Optional[pyvisa.resources.MessageBasedResource] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect(self) -> str:
        """Open VISA resource, reset device, configure for DC voltage
        sourcing with current measurement.  Return *IDN? string.

        On failure, raises :class:`visa_errors.ClassifiedVisaError`
        so callers can distinguish library-missing vs wrong-address
        vs timeout without parsing exception strings.  The original
        exception is preserved on ``err.original``.
        """
        from visa_errors import ClassifiedVisaError, classify
        try:
            self._rm = pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(self.visa_resource)
            self._inst.timeout = self.timeout_ms
            # full reset for clean state (ref: agilent_B29xx initialize)
            self._write("*RST")
            self._write("*CLS")
            self._write("SYST:BEEP:STAT OFF")
            idn = self._query("*IDN?")
            # voltage source, DC mode
            self._write(":SOUR:FUNC:MODE VOLT")
            self._write(":SOUR:VOLT:MODE FIX")
            # current measurement setup — SENS:FUNC is critical
            self._write(':SENS:FUNC "CURR"')
            self._write(f":SENS:CURR:PROT {self.current_compliance:.6g}")
            self._write(":SENS:CURR:RANG:AUTO ON")
            try:
                self._write(":SENS:CURR:RANG:AUTO:MODE RES")
            except Exception:
                pass  # B2910BL may not support this
            # ground terminal mode
            self._write(":OUTP:LOW GRO")
            return idn
        except ClassifiedVisaError:
            self._safe_release()
            raise
        except Exception as exc:
            self._safe_release()
            raise ClassifiedVisaError(
                classify(exc), exc,
                context=f"B2901 connect {self.visa_resource}") from exc

    def _safe_release(self) -> None:
        """Best-effort close of partially-opened VISA handles used by
        :meth:`connect` to keep the object in a reconnectable state
        after a classified failure.  Never raises.
        """
        try:
            if self._inst is not None:
                self._inst.close()
        except Exception:
            pass
        finally:
            self._inst = None
        try:
            if self._rm is not None:
                self._rm.close()
        except Exception:
            pass
        finally:
            self._rm = None

    def close(self) -> None:
        """Disable output, restore defaults, release VISA resources."""
        try:
            if self._inst is not None:
                try:
                    self._write(":OUTP OFF")
                    # restore NPLC to standard value
                    self._write(":SENS:CURR:NPLC 1")
                except Exception:
                    pass
                self._inst.close()
        except Exception:
            pass
        finally:
            self._inst = None
        try:
            if self._rm is not None:
                self._rm.close()
        except Exception:
            pass
        finally:
            self._rm = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def idn(self) -> str:
        return self._query("*IDN?")

    def read_voltage(self) -> float:
        return self._read_float(":MEAS:VOLT?")

    def read_current(self) -> float:
        return self._read_float(":MEAS:CURR?")

    def _read_float(self, cmd: str, retries: int = 3) -> float:
        """Query a numeric SCPI value with retry on empty/invalid response."""
        for attempt in range(retries):
            raw = self._query(cmd)
            if raw:
                try:
                    return float(raw)
                except ValueError:
                    pass
            # wait before retry — give SMU time to finish measurement
            import time
            time.sleep(0.05)
        raise ValueError(f"No valid numeric response for {cmd!r} "
                         f"after {retries} attempts (last: {raw!r})")

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def set_voltage(self, value: float) -> None:
        if not (self.v_min <= value <= self.v_max):
            raise ValueError(
                f"Requested voltage {value} V outside [{self.v_min}, {self.v_max}] V")
        self._write(f":SOUR:VOLT {value:.6g}")

    def set_current_limit(self, value: float) -> None:
        if not (0.0 <= value <= self.i_max):
            raise ValueError(
                f"Requested compliance {value} A outside [0, {self.i_max}] A")
        self.current_compliance = value
        self._write(f":SENS:CURR:PROT {value:.6g}")

    def output(self, enable: bool) -> None:
        self._write(":OUTP ON" if enable else ":OUTP OFF")

    def set_nplc(self, nplc: float) -> None:
        """Set current-measurement integration time in power-line cycles."""
        self._write(f":SENS:CURR:NPLC {nplc:.4g}")

    def is_in_compliance(self) -> bool:
        """Return True if the SMU is currently in compliance (current-limited)."""
        return bool(int(self._query(":STAT:QUES:COND?")) & 1)

    # Discrete current ranges supported by the B2900 family (A).
    # The driver clamps a requested value to the next *higher* hardware
    # range so the SMU does not refuse the SCPI command.
    CURRENT_RANGES_A: tuple[float, ...] = (
        1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1.0, 1.5, 3.0,
    )

    def set_current_range(self, value: float | None) -> None:
        """Configure the current measurement range.

        ``value`` semantics:

        * ``None`` – enable autorange (``:SENS:CURR:RANG:AUTO ON``)
        * ``float`` – disable autorange and pin a fixed range; the value
          is rounded up to the next supported hardware range.

        SCPI commands that some B29xx variants do not implement (e.g.
        ``:SENS:CURR:RANG:AUTO:MODE`` on the B2910BL) are written
        defensively – a refusal does not break the configuration.
        """
        if value is None:
            self._write(":SENS:CURR:RANG:AUTO ON")
            try:
                self._write(":SENS:CURR:RANG:AUTO:MODE RES")
            except Exception:
                pass  # not supported on all models
            return
        if value <= 0:
            raise ValueError(f"current range must be > 0 (got {value!r})")
        # round up to the next discrete supported range
        chosen = next((r for r in self.CURRENT_RANGES_A if r >= value),
                      self.CURRENT_RANGES_A[-1])
        self._write(":SENS:CURR:RANG:AUTO OFF")
        self._write(f":SENS:CURR:RANG {chosen:.6g}")

    # Valid enum values for set_autozero / set_averaging / set_output_low.
    # Kept here so the dialog and tests can reference them without
    # importing pyvisa.
    AUTOZERO_STATES: tuple[str, ...] = ("OFF", "ON", "ONCE")
    AVERAGING_MODES: tuple[str, ...] = ("REP", "MOV")
    OUTPUT_LOW_MODES: tuple[str, ...] = ("GRO", "FLO")

    def set_output_low(self, mode: str) -> None:
        """Configure the low-terminal topology.

        ``mode`` must be ``"GRO"`` (low terminal tied to chassis ground,
        factory default, correct for most DLP setups) or ``"FLO"``
        (floating).  Wrong choice + wrong wiring typically produces
        nonsense measurements – callers should surface this in the UI.
        """
        m = str(mode).upper()
        if m not in self.OUTPUT_LOW_MODES:
            raise ValueError(
                f"output-low mode must be one of {self.OUTPUT_LOW_MODES}, "
                f"got {mode!r}")
        self._write(f":OUTP:LOW {m}")

    def set_beep(self, enabled: bool) -> None:
        """Enable or disable the front-panel beeper.

        The driver's :meth:`connect` disables the beep by default; this
        setter lets the user opt back in.  Called after connect by the
        apply-path so the user choice wins over the init-time default.
        """
        self._write(":SYST:BEEP:STAT " + ("ON" if bool(enabled) else "OFF"))

    def set_remote_sense(self, enabled: bool) -> None:
        """Enable / disable 4-wire (remote sense) measurement.

        Writes the canonical B2900 form ``:SYST:RSEN ON|OFF`` first.
        If the underlying VISA write raises (e.g. older firmware that
        only knows the legacy ``:SENS:REM`` form), the call falls back
        defensively so a single-form rejection does not strand the
        instrument.

        The factory default after ``*RST`` is OFF (2-wire); this setter
        is therefore a true opt-in.  Wiring caveat: enabling 4-wire
        without a proper sense-lead connection produces an open
        voltage-measure loop and unreliable readings — callers must
        surface the option behind an explicit user choice.
        """
        state = "ON" if bool(enabled) else "OFF"
        try:
            self._write(f":SYST:RSEN {state}")
        except Exception:
            # Defensive fallback for firmware that only accepts the
            # older syntax — see legacy agilent_B29xx.py for precedent.
            self._write(f":SENS:REM {state}")

    def factory_reset(self) -> None:
        """Issue ``*RST`` and ``*CLS`` – factory defaults, errors cleared.

        Output is left OFF (B2900 behaviour on *RST).  The caller is
        responsible for re-applying any desired configuration via
        :func:`dlp_instrument_dialog.apply_instrument_options`.
        """
        self._write("*RST")
        self._write("*CLS")

    def set_autozero(self, state: str) -> None:
        """Configure the auto-zero behaviour.

        ``state`` must be one of ``"OFF"``, ``"ON"``, ``"ONCE"``.  ``OFF``
        is the fastest but drifts; ``ON`` re-zeroes periodically;
        ``ONCE`` triggers a single re-zero now and then keeps the value.
        """
        state = str(state).upper()
        if state not in self.AUTOZERO_STATES:
            raise ValueError(
                f"autozero state must be one of {self.AUTOZERO_STATES}, "
                f"got {state!r}")
        self._write(f":SENS:AZER:STAT {state}")

    def set_averaging(self, enabled: bool, count: int = 1,
                       mode: str = "REP") -> None:
        """Configure the hardware averaging filter.

        ``mode`` is ``"REP"`` (repeating) or ``"MOV"`` (moving average).
        ``count`` is clamped to ``[1, 100]``.  Setting ``enabled=False``
        still writes a sensible count so a later enable re-uses it.
        """
        mode = str(mode).upper()
        if mode not in self.AVERAGING_MODES:
            raise ValueError(
                f"averaging mode must be one of {self.AVERAGING_MODES}, "
                f"got {mode!r}")
        count = max(1, min(100, int(count)))
        self._write(f":SENS:AVER:TCON {mode}")
        self._write(f":SENS:AVER:COUN {count}")
        self._write(":SENS:AVER " + ("ON" if enabled else "OFF"))

    def set_source_delay(self, seconds: float) -> None:
        """Set the hardware source delay between set-voltage and measure.

        ``seconds`` must be >= 0.  Default 0 reproduces the pre-existing
        behaviour (no extra hardware delay beyond the software settle).
        """
        s = float(seconds)
        if s < 0:
            raise ValueError(f"source delay must be >= 0 (got {s!r})")
        self._write(f":SOUR:DEL {s:.6g}")

    def enable_output_protection(self, enable: bool = True) -> None:
        """Enable/disable automatic output-off on compliance trip.

        When enabled, the SMU switches output OFF if the compliance
        limit is reached.  This is a hardware safety feature —
        recommended for DLP measurements where probe short-circuits
        or arc events may occur.  Default after *RST is OFF.

        Ref: Keysight B2900 Programming Guide, :OUTP:PROT command.
        """
        self._write(":OUTP:PROT " + ("ON" if enable else "OFF"))

    # ------------------------------------------------------------------
    # Resource enumeration
    # ------------------------------------------------------------------
    @staticmethod
    def scan_visa_resources() -> list[tuple[str, str]]:
        """Return [(resource_string, idn), ...] for all reachable GPIB
        instruments.

        Per-resource probe errors are absorbed silently (an instrument
        that refuses ``*IDN?`` is just dropped from the list).  A
        failure to create the ResourceManager itself is the one case
        callers care about — that usually means "no VISA library" —
        so it is re-raised as :class:`ClassifiedVisaError` so the UI
        can surface the remediation hint.
        """
        from visa_errors import ClassifiedVisaError, classify
        try:
            rm = pyvisa.ResourceManager()
        except Exception as exc:
            raise ClassifiedVisaError(
                classify(exc), exc,
                context="B2901 VISA scan") from exc
        results: list[tuple[str, str]] = []
        try:
            for res in rm.list_resources("?*INSTR"):
                try:
                    inst = rm.open_resource(res, timeout=2000)
                    idn = inst.query("*IDN?").strip()
                    inst.close()
                    results.append((res, idn))
                except Exception:
                    pass
        finally:
            rm.close()
        return results

    def set_fixed_voltage(self, value: float, *, enable_output: bool = True) -> float:
        """Convenience: set voltage, optionally enable output, return measured V."""
        self.set_voltage(value)
        if enable_output:
            self.output(True)
            time.sleep(0.05)
        return self.read_voltage()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _write(self, cmd: str) -> None:
        if self._inst is None:
            raise RuntimeError("Not connected – call connect() first")
        self._inst.write(cmd)

    def _query(self, cmd: str) -> str:
        if self._inst is None:
            raise RuntimeError("Not connected – call connect() first")
        return self._inst.query(cmd).strip()


# ======================================================================
# Standalone convenience function
# ======================================================================
def set_b2901_voltage(
    voltage: float, *,
    resource: str = "GPIB0::23::INSTR",
    timeout: float = 2.0,
    v_min: float = -250.0,
    v_max: float = 250.0,
    current_compliance: float = 0.1,
    enable_output: bool = True,
) -> float:
    """One-shot: connect → set voltage → enable output → return measured V."""
    psu = KeysightB2901PSU(
        visa_resource=resource, timeout=timeout,
        v_min=v_min, v_max=v_max, current_compliance=current_compliance,
    )
    psu.connect()
    return psu.set_fixed_voltage(voltage, enable_output=enable_output)
