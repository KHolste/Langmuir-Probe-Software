"""Tests for the Keithley 2000 driver, fake, and DLP V3 integration.

Hardware-free: PyVISA is mocked the same way ``test_keysight_b2901``
does it.  A small at-the-end ``__main__`` block exposes a manual
hardware smoke-test path the bench operator can run with the real
K2000 attached at ``GPIB0::9::INSTR``.
"""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from keithley_2000 import Keithley2000DMM, read_k2000_voltage  # noqa: E402
from fake_keithley_2000 import FakeKeithley2000  # noqa: E402


# ===========================================================================
# Driver against mocked VISA
# ===========================================================================
@pytest.fixture()
def mock_visa():
    """Patch pyvisa.ResourceManager and provide a query-table mock."""
    with patch("keithley_2000.pyvisa.ResourceManager") as MockRM:
        rm = MockRM.return_value
        inst = MagicMock(name="instrument")
        rm.open_resource.return_value = inst
        # Default query routing — tests can override via inst.query.side_effect.
        responses = {
            "*IDN?": "KEITHLEY INSTRUMENTS,MODEL 2000,1234567,A02 /A02",
            ":READ?": "+6.00000E-01",   # ≈ 0.6 V — bench expectation
        }
        inst.query.side_effect = lambda cmd: responses.get(cmd, "0")
        yield rm, inst


class TestDriverConnect:
    def test_default_visa_resource_is_gpib0_9(self):
        d = Keithley2000DMM()
        assert d.visa_resource == "GPIB0::9::INSTR"
        assert Keithley2000DMM.DEFAULT_VISA == "GPIB0::9::INSTR"

    def test_connect_opens_correct_resource(self, mock_visa):
        rm, _ = mock_visa
        d = Keithley2000DMM(visa_resource="GPIB0::9::INSTR", timeout=3.0)
        idn = d.connect()
        rm.open_resource.assert_called_once_with("GPIB0::9::INSTR")
        assert "KEITHLEY" in idn.upper()
        assert "2000" in idn

    def test_connect_writes_dc_volt_configuration(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        writes = [c.args[0] for c in inst.write.call_args_list]
        assert "*RST" in writes
        assert "*CLS" in writes
        assert ":CONF:VOLT:DC" in writes
        # Default = autorange
        assert ":SENS:VOLT:DC:RANG:AUTO ON" in writes
        assert any(w.startswith(":SENS:VOLT:DC:NPLC ") for w in writes)

    def test_connect_with_fixed_range_disables_autorange(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM(v_range=10.0)
        d.connect()
        writes = [c.args[0] for c in inst.write.call_args_list]
        assert ":SENS:VOLT:DC:RANG:AUTO OFF" in writes
        assert ":SENS:VOLT:DC:RANG 10" in writes

    def test_timeout_is_applied_in_milliseconds(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM(timeout=2.5)
        d.connect()
        # The driver assigns timeout_ms onto the underlying instrument.
        assert inst.timeout == 2500

    def test_close_releases_visa_resources(self, mock_visa):
        rm, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        d.close()
        inst.close.assert_called_once()
        rm.close.assert_called_once()
        assert d._inst is None
        assert d._rm is None


class TestDriverReadVoltage:
    def test_read_voltage_returns_float_around_06(self, mock_visa):
        d = Keithley2000DMM()
        d.connect()
        v = d.read_voltage()
        assert isinstance(v, float)
        assert v == pytest.approx(0.6, abs=0.05)

    def test_read_voltage_uses_read_query(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        inst.query.reset_mock()
        d.read_voltage()
        # Only :READ? must be issued — we deliberately do NOT use
        # :MEAS:VOLT:DC? because that would re-issue :CONF defaults.
        inst.query.assert_called_with(":READ?")

    def test_read_voltage_parses_scientific_notation(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        inst.query.side_effect = lambda cmd: {
            "*IDN?": "KEITHLEY INSTRUMENTS,MODEL 2000",
            ":READ?": "-1.234567E-02",
        }.get(cmd, "0")
        v = d.read_voltage()
        assert v == pytest.approx(-0.01234567, rel=1e-6)

    def test_read_voltage_parses_plain_decimal(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        inst.query.side_effect = lambda cmd: {
            "*IDN?": "KEITHLEY INSTRUMENTS,MODEL 2000",
            ":READ?": "0.6004321",
        }.get(cmd, "0")
        assert d.read_voltage() == pytest.approx(0.6004321, abs=1e-7)

    def test_read_voltage_retries_on_empty_response(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        responses = iter(["", "", "+6.00000E-01"])
        inst.query.side_effect = lambda cmd: (
            next(responses) if cmd == ":READ?" else "KEITHLEY,2000")
        v = d.read_voltage()
        assert v == pytest.approx(0.6, abs=0.05)

    def test_read_voltage_raises_after_repeated_garbage(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        inst.query.side_effect = lambda cmd: (
            "not a number" if cmd == ":READ?" else "KEITHLEY,2000")
        with pytest.raises(ValueError):
            d.read_voltage()

    def test_read_voltage_without_connect_raises(self):
        d = Keithley2000DMM()
        with pytest.raises(RuntimeError):
            d.read_voltage()


class TestDriverSetters:
    def test_set_voltage_range_autorange(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        inst.write.reset_mock()
        d.set_voltage_range(None)
        writes = [c.args[0] for c in inst.write.call_args_list]
        assert ":SENS:VOLT:DC:RANG:AUTO ON" in writes
        assert d.v_range is None

    def test_set_voltage_range_fixed(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        inst.write.reset_mock()
        d.set_voltage_range(1.0)
        writes = [c.args[0] for c in inst.write.call_args_list]
        assert ":SENS:VOLT:DC:RANG:AUTO OFF" in writes
        assert ":SENS:VOLT:DC:RANG 1" in writes
        assert d.v_range == 1.0

    def test_set_voltage_range_rejects_zero(self, mock_visa):
        d = Keithley2000DMM()
        d.connect()
        with pytest.raises(ValueError):
            d.set_voltage_range(0)

    def test_set_nplc_writes_value(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM()
        d.connect()
        inst.write.reset_mock()
        d.set_nplc(0.1)
        writes = [c.args[0] for c in inst.write.call_args_list]
        assert ":SENS:VOLT:DC:NPLC 0.1" in writes
        assert d.nplc == pytest.approx(0.1)

    def test_set_nplc_rejects_zero(self, mock_visa):
        d = Keithley2000DMM()
        d.connect()
        with pytest.raises(ValueError):
            d.set_nplc(0)


class TestStandaloneHelper:
    def test_read_k2000_voltage_round_trip(self, mock_visa):
        v = read_k2000_voltage(resource="GPIB0::9::INSTR")
        assert v == pytest.approx(0.6, abs=0.05)


# ===========================================================================
# RS232 transport
# ===========================================================================
class TestRS232Transport:
    def test_default_transport_is_gpib(self):
        d = Keithley2000DMM()
        assert d.transport == "GPIB"

    def test_invalid_transport_raises(self):
        with pytest.raises(ValueError):
            Keithley2000DMM(transport="LAN")

    def test_rs232_resource_string_from_com(self):
        d = Keithley2000DMM(transport="RS232", port="COM3")
        assert d._resource_string() == "ASRL3::INSTR"

    def test_rs232_resource_string_passes_through_asrl(self):
        d = Keithley2000DMM(transport="RS232", port="ASRL7::INSTR")
        assert d._resource_string() == "ASRL7::INSTR"

    def test_rs232_connect_opens_asrl_resource_with_baud(self, mock_visa):
        rm, inst = mock_visa
        d = Keithley2000DMM(transport="RS232", port="COM4", baud=19200)
        d.connect()
        rm.open_resource.assert_called_once_with("ASRL4::INSTR")
        # baud_rate is set as an attribute on the VISA resource.
        assert inst.baud_rate == 19200
        # Termination characters set for the K2000 RS232 default.
        assert inst.read_termination == "\r"
        assert inst.write_termination == "\r"

    def test_rs232_default_baud_is_9600(self):
        d = Keithley2000DMM(transport="RS232", port="COM1")
        assert d.baud == 9600

    def test_gpib_path_does_not_touch_serial_attrs(self, mock_visa):
        _, inst = mock_visa
        d = Keithley2000DMM(transport="GPIB", visa_resource="GPIB0::9::INSTR")
        d.connect()
        # baud_rate should never have been written for the GPIB path.
        assert "baud_rate" not in vars(inst).get("__dict__", vars(inst))


# ===========================================================================
# V3 GUI: transport combo + RS232 fields
# ===========================================================================
class TestV3TransportSelector:
    def test_transport_combo_present(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        assert hasattr(win, "cmbK2000Transport")
        assert hasattr(win, "stackK2000Transport")
        items = [win.cmbK2000Transport.itemText(i)
                 for i in range(win.cmbK2000Transport.count())]
        assert items == ["GPIB", "RS232"]

    def test_rs232_fields_present(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        assert hasattr(win, "editK2000Port")
        assert hasattr(win, "cmbK2000Baud")
        assert win.cmbK2000Baud.currentText() == "9600"
        assert win.editK2000Port.text() == "COM1"

    def test_stack_swaps_with_combo(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        # Default: GPIB page (index 0).
        assert win.stackK2000Transport.currentIndex() == 0
        win.cmbK2000Transport.setCurrentIndex(1)
        assert win.stackK2000Transport.currentIndex() == 1
        win.cmbK2000Transport.setCurrentIndex(0)
        assert win.stackK2000Transport.currentIndex() == 0

    def test_baud_combo_has_standard_rates(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        rates = [int(win.cmbK2000Baud.itemData(i))
                 for i in range(win.cmbK2000Baud.count())]
        for expected in (1200, 9600, 115200):
            assert expected in rates


# ===========================================================================
# Fake instrument
# ===========================================================================
class TestFakeKeithley2000:
    def test_connect_returns_idn(self):
        f = FakeKeithley2000()
        idn = f.connect()
        assert "KEITHLEY" in idn.upper()
        assert "2000" in idn
        assert f.is_connected is True

    def test_default_voltage_is_06(self):
        f = FakeKeithley2000()
        f.connect()
        assert f.read_voltage() == pytest.approx(0.6)

    def test_custom_voltage(self):
        f = FakeKeithley2000(voltage=1.234)
        f.connect()
        assert f.read_voltage() == pytest.approx(1.234)

    def test_noise_scatters_around_target(self):
        f = FakeKeithley2000(voltage=0.6, noise_std=0.001, seed=42)
        f.connect()
        samples = [f.read_voltage() for _ in range(200)]
        mean = sum(samples) / len(samples)
        assert mean == pytest.approx(0.6, abs=0.005)
        # Ensure samples are not all identical (noise actually applied).
        assert len(set(samples)) > 1

    def test_close_marks_disconnected(self):
        f = FakeKeithley2000()
        f.connect()
        f.close()
        assert f.is_connected is False

    def test_read_before_connect_raises(self):
        f = FakeKeithley2000()
        with pytest.raises(RuntimeError):
            f.read_voltage()

    def test_idn_before_connect_raises(self):
        f = FakeKeithley2000()
        with pytest.raises(RuntimeError):
            f.idn()

    def test_setters_validate(self):
        f = FakeKeithley2000()
        f.connect()
        f.set_voltage_range(10.0)
        assert f.v_range == 10.0
        f.set_voltage_range(None)
        assert f.v_range is None
        with pytest.raises(ValueError):
            f.set_voltage_range(-1)
        f.set_nplc(0.5)
        assert f.nplc == pytest.approx(0.5)
        with pytest.raises(ValueError):
            f.set_nplc(0)

    def test_set_voltage_for_test_helper(self):
        f = FakeKeithley2000(voltage=0.6)
        f.connect()
        assert f.read_voltage() == pytest.approx(0.6)
        f.set_voltage_for_test(1.5)
        assert f.read_voltage() == pytest.approx(1.5)


# ===========================================================================
# DLP V3 GUI integration
# ===========================================================================
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestV3WindowConstruction:
    def test_window_has_k2000_widgets(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        for name in ("editK2000Visa", "chkK2000Sim", "btnK2000Connect",
                     "ledK2000", "lblK2000Idn", "btnK2000Read",
                     "lblK2000Value"):
            assert hasattr(win, name), name

    def test_default_visa_in_text_field(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        assert win.editK2000Visa.text() == "GPIB0::9::INSTR"

    def test_read_button_disabled_before_connect(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        assert win.btnK2000Read.isEnabled() is False

    def test_initial_value_label_shows_dash(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        assert "V" in win.lblK2000Value.text()
        assert win.lblK2000Value.text().startswith("\u2014")


class TestV3SimWorkflow:
    def test_sim_connect_enables_read_button(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        win.chkK2000Sim.setChecked(True)
        win._toggle_k2000_connect()
        try:
            assert win.k2000 is not None
            assert win.btnK2000Read.isEnabled() is True
            assert "KEITHLEY" in win.lblK2000Idn.text().upper()
        finally:
            win._toggle_k2000_connect()  # disconnect

    def test_sim_read_displays_06v(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        win.chkK2000Sim.setChecked(True)
        win._toggle_k2000_connect()
        try:
            win._read_k2000_voltage()
            text = win.lblK2000Value.text()
            assert text.endswith("V")
            # Strip sign and " V" suffix → numeric value
            numeric = float(text.replace("V", "").strip())
            assert numeric == pytest.approx(0.6, abs=0.05)
        finally:
            win._toggle_k2000_connect()

    def test_disconnect_disables_read_button(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        win.chkK2000Sim.setChecked(True)
        win._toggle_k2000_connect()
        win._toggle_k2000_connect()  # toggle off
        assert win.k2000 is None
        assert win.btnK2000Read.isEnabled() is False

    def test_read_without_connect_does_not_crash(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        # k2000 is None — must log + return, not raise.
        win._read_k2000_voltage()
        assert win.k2000 is None


# ===========================================================================
# Manual hardware smoke-test (skipped by default)
#
# Run with:
#     pytest tests/test_keithley_2000.py::test_real_hardware_voltage \
#            --run-hardware
# (requires the K2000 to be connected at GPIB0::9::INSTR with a known
#  ~0.6 V source attached to its INPUT HI/LO terminals).
# ===========================================================================
def pytest_addoption(parser):  # pragma: no cover - pytest hook
    parser.addoption("--run-hardware", action="store_true", default=False,
                     help="Enable real-instrument tests against GPIB0::9.")


@pytest.mark.skipif(
    "--run-hardware" not in sys.argv,
    reason="Requires real K2000 on GPIB0::9 — pass --run-hardware to enable.",
)
def test_real_hardware_voltage():  # pragma: no cover - hardware path
    """Smoke-test against the actual instrument.

    Expects roughly 0.6 V on the bench rig.  Adjust the tolerance if
    your reference source drifts more.
    """
    v = read_k2000_voltage(resource="GPIB0::9::INSTR")
    assert v == pytest.approx(0.6, abs=0.1), (
        f"K2000 reading {v} V outside the expected 0.6 V ± 0.1 V window")
