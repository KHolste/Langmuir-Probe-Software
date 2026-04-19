"""Tests for :mod:`visa_errors` — VISA / instrument failure
classification and operator-visible surfacing.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyvisa  # noqa: E402
from pyvisa import constants as _vc  # noqa: E402
from pyvisa.errors import VisaIOError, LibraryError  # noqa: E402

from visa_errors import (  # noqa: E402
    ClassifiedVisaError, VisaErrorKind, classify, format_for_operator,
    REMEDIATION,
)


# ---------------------------------------------------------------------------
# classify() — taxonomy coverage.
# ---------------------------------------------------------------------------
class TestClassification:
    def test_library_error_is_no_visa(self):
        # LibraryError signals "no VISA backend loadable"; the most
        # important case for an operator on a fresh machine.
        exc = LibraryError("No VISA library found.")
        assert classify(exc) == VisaErrorKind.NO_VISA

    def test_rsrc_nfound_is_no_device(self):
        exc = VisaIOError(_vc.VI_ERROR_RSRC_NFOUND)
        assert classify(exc) == VisaErrorKind.NO_DEVICE

    def test_timeout_is_timeout(self):
        exc = VisaIOError(_vc.VI_ERROR_TMO)
        assert classify(exc) == VisaErrorKind.TIMEOUT

    def test_library_nfound_is_no_visa(self):
        exc = VisaIOError(_vc.VI_ERROR_LIBRARY_NFOUND)
        assert classify(exc) == VisaErrorKind.NO_VISA

    def test_oserror_is_transport(self):
        exc = OSError(13, "Permission denied")  # COM port in use
        assert classify(exc) == VisaErrorKind.TRANSPORT

    def test_message_keyword_no_visa(self):
        # Backend-layer exception with a helpful message but not a
        # VisaIOError subclass — keyword-heuristic must still catch it.
        exc = RuntimeError("Could not open VISA library: visa64.dll not found")
        assert classify(exc) == VisaErrorKind.NO_VISA

    def test_message_keyword_serial(self):
        exc = RuntimeError("ASRL COM4 port is already in use")
        assert classify(exc) == VisaErrorKind.TRANSPORT

    def test_unknown_fallthrough(self):
        exc = RuntimeError("asdf qwer something nobody expected")
        assert classify(exc) == VisaErrorKind.UNKNOWN


# ---------------------------------------------------------------------------
# ClassifiedVisaError + operator-message formatting.
# ---------------------------------------------------------------------------
class TestOperatorMessage:
    def test_message_contains_kind_and_hint(self):
        exc = VisaIOError(_vc.VI_ERROR_RSRC_NFOUND)
        cve = ClassifiedVisaError(classify(exc), exc,
                                    context="K2000 connect")
        msg = str(cve)
        assert "K2000 connect" in msg
        assert "no_device" in msg
        # Remediation hint ends up in parentheses at the tail.
        assert REMEDIATION[VisaErrorKind.NO_DEVICE].split(".")[0] in msg

    def test_format_for_operator_raw_exception(self):
        # Accepting a raw exception lets call sites swap in
        # classification without restructuring — important for
        # callers that don't want to catch our custom type.
        exc = VisaIOError(_vc.VI_ERROR_TMO)
        msg = format_for_operator(exc, context="B2901 read")
        assert "B2901 read" in msg
        assert "timeout" in msg
        assert "timeout" in msg.lower()

    def test_format_includes_remediation_for_no_visa(self):
        exc = LibraryError("No VISA library found")
        msg = format_for_operator(exc)
        assert "Keysight IO Libraries" in msg or "NI-VISA" in msg

    def test_remediation_table_covers_all_kinds(self):
        # Protects the renderer from a future kind addition that
        # forgets its hint; every enum member must be mapped.
        for kind in VisaErrorKind:
            assert kind in REMEDIATION
            assert REMEDIATION[kind]


# ---------------------------------------------------------------------------
# Driver connect paths must raise ClassifiedVisaError.
# ---------------------------------------------------------------------------
class TestDriverConnectIntegration:
    def test_b2901_connect_raises_classified_on_rsrc_nfound(self, monkeypatch):
        import keysight_b2901 as m
        def _fake_rm_class():
            class _RM:
                def open_resource(self, res, *a, **k):
                    raise VisaIOError(_vc.VI_ERROR_RSRC_NFOUND)
                def close(self): pass
            return _RM()
        monkeypatch.setattr(m, "pyvisa", pyvisa)
        monkeypatch.setattr(m.pyvisa, "ResourceManager", _fake_rm_class)
        psu = m.KeysightB2901PSU(visa_resource="GPIB0::99::INSTR")
        with pytest.raises(ClassifiedVisaError) as ei:
            psu.connect()
        assert ei.value.kind == VisaErrorKind.NO_DEVICE
        assert "B2901 connect" in str(ei.value)

    def test_b2901_connect_raises_classified_on_library(self, monkeypatch):
        import keysight_b2901 as m
        def _boom():
            raise LibraryError("No VISA library found")
        monkeypatch.setattr(m.pyvisa, "ResourceManager", _boom)
        psu = m.KeysightB2901PSU(visa_resource="GPIB0::23::INSTR")
        with pytest.raises(ClassifiedVisaError) as ei:
            psu.connect()
        assert ei.value.kind == VisaErrorKind.NO_VISA

    def test_b2901_scan_raises_classified_on_library(self, monkeypatch):
        import keysight_b2901 as m
        def _boom():
            raise LibraryError("No VISA library found")
        monkeypatch.setattr(m.pyvisa, "ResourceManager", _boom)
        with pytest.raises(ClassifiedVisaError) as ei:
            m.KeysightB2901PSU.scan_visa_resources()
        assert ei.value.kind == VisaErrorKind.NO_VISA

    def test_k2000_connect_raises_classified_on_timeout(self, monkeypatch):
        import keithley_2000 as m
        def _fake_rm():
            class _RM:
                def open_resource(self, *a, **k):
                    raise VisaIOError(_vc.VI_ERROR_TMO)
                def close(self): pass
            return _RM()
        monkeypatch.setattr(m.pyvisa, "ResourceManager", _fake_rm)
        dmm = m.Keithley2000DMM()
        with pytest.raises(ClassifiedVisaError) as ei:
            dmm.connect()
        assert ei.value.kind == VisaErrorKind.TIMEOUT
        assert "K2000 connect" in str(ei.value)


# ---------------------------------------------------------------------------
# The successful-connect path (against a fake pyvisa ResourceManager) is
# NOT broken by the wrapping — a connect returning *IDN? must still
# succeed cleanly.
# ---------------------------------------------------------------------------
class TestSuccessfulConnectUnchanged:
    def test_b2901_connect_success_returns_idn(self, monkeypatch):
        import keysight_b2901 as m
        class _Inst:
            def __init__(self):
                self._last = ""
                self.timeout = 0
            def write(self, cmd): self._last = cmd
            def query(self, _cmd): return "Keysight,B2901A,FAKE,1.0\n"
            def close(self): pass
        class _RM:
            def open_resource(self, *a, **k): return _Inst()
            def close(self): pass
        monkeypatch.setattr(m.pyvisa, "ResourceManager", lambda: _RM())
        psu = m.KeysightB2901PSU(visa_resource="GPIB0::23::INSTR")
        idn = psu.connect()
        assert "B2901A" in idn
        psu.close()

    def test_k2000_connect_success_returns_idn(self, monkeypatch):
        import keithley_2000 as m
        class _Inst:
            def __init__(self):
                self.timeout = 0
                # PyVISA serial attributes are setattr-style; tolerate
                # arbitrary assignment during _configure_serial.
            def __setattr__(self, k, v): object.__setattr__(self, k, v)
            def write(self, cmd): pass
            def query(self, _cmd): return "KEITHLEY,2000,FAKE,B05"
            def close(self): pass
        class _RM:
            def open_resource(self, *a, **k): return _Inst()
            def close(self): pass
        monkeypatch.setattr(m.pyvisa, "ResourceManager", lambda: _RM())
        dmm = m.Keithley2000DMM()
        idn = dmm.connect()
        assert "KEITHLEY" in idn
        dmm.close()
