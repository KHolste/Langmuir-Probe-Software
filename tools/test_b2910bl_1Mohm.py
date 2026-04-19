"""
Validierungsskript: Keysight B2910BL mit ~1 MΩ Lastwiderstand.

Verbindet sich mit der SMU, sourced 1 V, misst den Strom,
und prüft ob der Wert nahe 1.035 uA liegt (= 1 V / 966 kΩ).

Anschluss: Widerstand ~1 MΩ zwischen Force+ und Force-.

Verwendung:
    python tools/test_b2910bl_1Mohm.py [VISA_RESOURCE]

Beispiel:
    python tools/test_b2910bl_1Mohm.py GPIB0::23::INSTR
"""
import sys
import time
sys.path.insert(0, ".")
from keysight_b2901 import KeysightB2901PSU


def main():
    resource = sys.argv[1] if len(sys.argv) > 1 else "GPIB0::23::INSTR"
    print(f"Connecting to {resource}...")

    smu = KeysightB2901PSU(
        visa_resource=resource,
        timeout=5.0,
        current_compliance=0.001,  # 1 mA — mehr als genug für 1 uA
    )

    try:
        idn = smu.connect()
        print(f"Connected: {idn}")

        # Zusätzliche Diagnose: welche SCPI-Settings sind aktiv?
        print("\n--- Diagnose ---")
        print(f"  Source mode:  {smu._query(':SOUR:FUNC:MODE?')}")
        print(f"  Sense func:  {smu._query(':SENS:FUNC?')}")
        print(f"  Curr range:  {smu._query(':SENS:CURR:RANG?')}")
        print(f"  Curr auto:   {smu._query(':SENS:CURR:RANG:AUTO?')}")
        print(f"  Compliance:  {smu._query(':SENS:CURR:PROT?')}")
        print(f"  NPLC:        {smu._query(':SENS:CURR:NPLC?')}")
        print(f"  Output low:  {smu._query(':OUTP:LOW?')}")

        # NPLC auf 1 für gute Auflösung
        smu.set_nplc(1.0)

        # Output ON, 0 V zuerst
        smu.set_voltage(0.0)
        smu.output(True)
        time.sleep(0.5)

        # Offset-Messung bei 0 V
        i_zero = smu.read_current()
        print(f"\n--- Offset bei 0 V ---")
        print(f"  I(0V) = {i_zero:.6e} A = {i_zero*1e6:.4f} uA")

        # Messung bei 1 V
        smu.set_voltage(1.0)
        time.sleep(1.0)  # ausreichend settlen lassen
        i_1v = smu.read_current()
        v_1v = smu.read_voltage()

        print(f"\n--- Messung bei 1 V ---")
        print(f"  V(1V) = {v_1v:.6f} V")
        print(f"  I(1V) = {i_1v:.6e} A = {i_1v*1e6:.4f} uA")

        # Widerstand berechnen
        if abs(i_1v) > 1e-12:
            r_calc = v_1v / i_1v
            print(f"  R     = {r_calc:.0f} Ohm = {r_calc/1e6:.3f} MOhm")

        # Referenzvergleich
        i_expected = 1.035e-6  # uA
        if abs(i_1v) > 0:
            ratio = i_1v / i_expected
            print(f"\n--- Vergleich ---")
            print(f"  Erwartet: {i_expected*1e6:.3f} uA")
            print(f"  Gemessen: {i_1v*1e6:.4f} uA")
            print(f"  Verhältnis: {ratio:.4f}")
            if 0.9 < ratio < 1.1:
                print(f"  OK PASS (innerhalb +-10%)")
            else:
                print(f"  FAIL FAIL (Abweichung > 10%)")

        # Sweep 0..5 V in 1 V Schritten
        print(f"\n--- Quick Sweep 0-5 V ---")
        print(f"  {'V_set':>8s}  {'V_meas':>10s}  {'I_meas':>12s}  {'I_uA':>10s}")
        for v in [0, 1, 2, 3, 4, 5]:
            smu.set_voltage(float(v))
            time.sleep(0.5)
            vm = smu.read_voltage()
            im = smu.read_current()
            print(f"  {v:8.1f}  {vm:10.6f}  {im:12.6e}  {im*1e6:10.4f}")

    finally:
        smu.set_voltage(0.0)
        smu.output(False)
        smu.close()
        print("\nSMU disconnected.")


if __name__ == "__main__":
    main()
