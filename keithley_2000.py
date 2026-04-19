"""Keithley 2000 6.5-digit DMM — minimal PyVISA/SCPI driver.

Scope of this driver (DLP V3, first iteration): connect to the
multimeter over GPIB and read DC voltage on demand.  Higher-level
functions (continuous logging, DCI/ACV/RES, scanner card support) are
intentionally not included yet.

Standalone — no Qt dependency.  Mirrors the surface of
``keysight_b2901.KeysightB2901PSU`` (``connect`` / ``close`` / ``idn``
/ ``read_voltage`` / ``_write`` / ``_query``) so the application code
can treat both instruments via the same idioms.
"""
from __future__ import annotations

import time
from typing import Optional

import pyvisa


#: Transports supported by the K2000 driver.
TRANSPORTS = ("GPIB", "RS232")

#: Baud rates exposed in the GUI / accepted by the driver.
BAUD_RATES = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)

#: Default RS232 settings — match the K2000 power-up defaults so a
#: freshly-flipped front-panel switch "just works".
DEFAULT_BAUD = 9600
DEFAULT_PARITY = "N"
DEFAULT_DATA_BITS = 8
DEFAULT_STOP_BITS = 1
DEFAULT_SERIAL_PORT = "COM1"


class Keithley2000DMM:
    """Driver for the Keithley 2000 DMM over VISA (GPIB / USB / LAN / RS232).

    Defaults are tuned for the JLU-IPI bench: GPIB primary address 9,
    DC-voltage measurement, autorange, NPLC 1 (≈20 ms @ 50 Hz).  When
    ``transport='RS232'`` is selected the driver builds an
    ``ASRL{N}::INSTR`` resource string from the configured COM port and
    applies the serial parameters via PyVISA attributes after opening.
    """

    DEFAULT_VISA = "GPIB0::9::INSTR"

    def __init__(
        self,
        *,
        visa_resource: str = DEFAULT_VISA,
        timeout: float = 2.0,
        v_range: float | None = None,   # None → autorange
        nplc: float = 1.0,
        transport: str = "GPIB",
        port: str = DEFAULT_SERIAL_PORT,
        baud: int = DEFAULT_BAUD,
        parity: str = DEFAULT_PARITY,
        data_bits: int = DEFAULT_DATA_BITS,
        stop_bits: float = DEFAULT_STOP_BITS,
    ) -> None:
        self.visa_resource = visa_resource
        self.timeout_ms = int(timeout * 1000)
        self.v_range = v_range
        self.nplc = float(nplc)
        t = str(transport).upper()
        if t not in TRANSPORTS:
            raise ValueError(
                f"transport must be one of {TRANSPORTS}, got {transport!r}")
        self.transport = t
        self.port = str(port)
        self.baud = int(baud)
        self.parity = (str(parity).upper()[:1] if parity else "N")
        self.data_bits = int(data_bits)
        self.stop_bits = float(stop_bits)
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst: Optional[pyvisa.resources.MessageBasedResource] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _resource_string(self) -> str:
        """Resolve the actual VISA resource string for the chosen transport.

        * GPIB: caller-provided ``visa_resource`` is used verbatim.
        * RS232: ``COMn`` → ``ASRLn::INSTR`` (PyVISA serial convention).
                 An already-formed ``ASRL...::INSTR`` string is passed
                 through unchanged so power users keep full control.
        """
        if self.transport == "GPIB":
            return self.visa_resource
        p = self.port.strip().upper()
        if p.startswith("ASRL"):
            return p
        if p.startswith("COM"):
            return f"ASRL{p[3:]}::INSTR"
        return self.port

    def _configure_serial(self) -> None:
        """Apply RS232 line settings on the open VISA resource.

        Each setter is wrapped defensively so a backend that does not
        expose a particular attribute (rare on RS232, but common on
        simulators) does not abort the connect.
        """
        inst = self._inst
        try:
            inst.baud_rate = self.baud
        except Exception:
            pass
        try:
            inst.data_bits = self.data_bits
        except Exception:
            pass
        try:
            from pyvisa.constants import Parity, StopBits
            inst.parity = {"N": Parity.none, "E": Parity.even,
                            "O": Parity.odd}.get(self.parity, Parity.none)
            inst.stop_bits = {1: StopBits.one, 2: StopBits.two,
                               1.5: StopBits.one_and_a_half}.get(
                self.stop_bits, StopBits.one)
        except Exception:
            pass
        # Keithley 2000 RS232 default terminator is CR — set both
        # directions so query() and write() round-trip cleanly.
        try:
            inst.read_termination = "\r"
            inst.write_termination = "\r"
        except Exception:
            pass

    def connect(self) -> str:
        """Open VISA resource, reset, configure for DC-voltage measure.

        Returns the ``*IDN?`` string of the instrument.  On failure,
        raises :class:`visa_errors.ClassifiedVisaError` so the UI can
        surface a specific remediation (install VISA, check address,
        check COM port) rather than a bare exception string.
        """
        from visa_errors import ClassifiedVisaError, classify
        try:
            self._rm = pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(self._resource_string())
            self._inst.timeout = self.timeout_ms
            if self.transport == "RS232":
                self._configure_serial()
            self._write("*RST")
            self._write("*CLS")
            idn = self._query("*IDN?")
            # Configure DC voltage measurement
            self._write(":CONF:VOLT:DC")
            # Range: autorange unless caller pinned a fixed range.
            if self.v_range is None:
                self._write(":SENS:VOLT:DC:RANG:AUTO ON")
            else:
                self._write(":SENS:VOLT:DC:RANG:AUTO OFF")
                self._write(f":SENS:VOLT:DC:RANG {float(self.v_range):.6g}")
            # Integration time
            self._write(f":SENS:VOLT:DC:NPLC {self.nplc:.4g}")
            return idn
        except ClassifiedVisaError:
            self._safe_release()
            raise
        except Exception as exc:
            self._safe_release()
            raise ClassifiedVisaError(
                classify(exc), exc,
                context=f"K2000 connect {self._resource_string()}") from exc

    def _safe_release(self) -> None:
        """Best-effort close of partially-opened VISA handles so a
        classified failure does not leave the driver in a half-open
        state.  Never raises.
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

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def idn(self) -> str:
        return self._query("*IDN?")

    def read_voltage(self) -> float:
        """Trigger a single DC-voltage measurement and return it in volts.

        Uses ``:READ?`` rather than ``:MEAS:VOLT:DC?`` so the previously
        configured range / NPLC / autozero settings are preserved
        (``:MEAS?`` would re-issue ``:CONF`` defaults).
        """
        return self._read_float(":READ?")

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def set_voltage_range(self, v_range: float | None) -> None:
        """Pin a fixed range, or pass ``None`` to enable autorange."""
        if v_range is None:
            self._write(":SENS:VOLT:DC:RANG:AUTO ON")
        else:
            r = float(v_range)
            if r <= 0:
                raise ValueError(f"voltage range must be > 0 (got {r!r})")
            self._write(":SENS:VOLT:DC:RANG:AUTO OFF")
            self._write(f":SENS:VOLT:DC:RANG {r:.6g}")
        self.v_range = v_range

    def set_nplc(self, nplc: float) -> None:
        """Set integration time in power-line cycles (typical: 0.01 … 10)."""
        n = float(nplc)
        if n <= 0:
            raise ValueError(f"nplc must be > 0 (got {n!r})")
        self._write(f":SENS:VOLT:DC:NPLC {n:.4g}")
        self.nplc = n

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _read_float(self, cmd: str, retries: int = 3) -> float:
        """Query a numeric SCPI value with retry on empty/invalid response."""
        raw = ""
        for _ in range(retries):
            raw = self._query(cmd)
            if raw:
                try:
                    return float(raw)
                except ValueError:
                    pass
            time.sleep(0.05)
        raise ValueError(
            f"No valid numeric response for {cmd!r} after {retries} "
            f"attempts (last: {raw!r})")

    def _write(self, cmd: str) -> None:
        if self._inst is None:
            raise RuntimeError("Not connected — call connect() first")
        self._inst.write(cmd)

    def _query(self, cmd: str) -> str:
        if self._inst is None:
            raise RuntimeError("Not connected — call connect() first")
        return self._inst.query(cmd).strip()


# ======================================================================
# Standalone convenience function
# ======================================================================
def read_k2000_voltage(
    *,
    resource: str = Keithley2000DMM.DEFAULT_VISA,
    timeout: float = 2.0,
    nplc: float = 1.0,
) -> float:
    """One-shot helper: connect → read voltage → close → return value."""
    dmm = Keithley2000DMM(visa_resource=resource, timeout=timeout, nplc=nplc)
    try:
        dmm.connect()
        return dmm.read_voltage()
    finally:
        dmm.close()
