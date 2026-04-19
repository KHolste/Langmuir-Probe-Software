#!/usr/bin/env python3
"""
Keysight B2901A – Spannungsrampen-Test mit Soll/Ist-Vergleich.

Fährt eine Spannungsrampe in konfigurierbaren Schritten und gibt für
jeden Punkt Sollwert, echten Istwert (via :MEAS:VOLT?) und die
Differenz aus.  Testet mehrere Rampengeschwindigkeiten hintereinander.

Nutzung:
    python tools/b2901_ramp_test.py
    python tools/b2901_ramp_test.py --resource GPIB0::23::INSTR
    python tools/b2901_ramp_test.py --start 0 --stop 50 --step 5
    python tools/b2901_ramp_test.py --delays 0.05,0.2,1.0
    python tools/b2901_ramp_test.py --compliance 0.01

SCPI-Befehle:
    Sollwert setzen:  :SOUR:VOLT <value>
    Istwert lesen:    :MEAS:VOLT?   (echte Messung am Ausgang)
    Sollwert lesen:   :SOUR:VOLT?   (nur der gesetzte Wert, keine Messung)
"""
from __future__ import annotations

import argparse
import sys
import os
import time

# Projektverzeichnis zum Pfad hinzufügen, damit keysight_b2901 importierbar ist
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from keysight_b2901 import KeysightB2901PSU


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="B2901A Spannungsrampen-Test: Soll vs. Ist",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--resource", default="GPIB0::23::INSTR",
                   help="VISA-Resource-String (default: GPIB0::23::INSTR)")
    p.add_argument("--start", type=float, default=0.0,
                   help="Startspannung in V (default: 0)")
    p.add_argument("--stop", type=float, default=50.0,
                   help="Endspannung in V (default: 50)")
    p.add_argument("--step", type=float, default=5.0,
                   help="Schrittweite in V (default: 5)")
    p.add_argument("--delays", default="0.05,0.2,1.0",
                   help="Kommaseparierte Wartezeiten pro Schritt in Sekunden "
                        "(default: 0.05,0.2,1.0)")
    p.add_argument("--compliance", type=float, default=0.1,
                   help="Stromkompliance in A (default: 0.1)")
    p.add_argument("--timeout", type=float, default=5.0,
                   help="VISA-Timeout in Sekunden (default: 5)")
    p.add_argument("--v-max", type=float, default=250.0,
                   help="Maximale Spannung (Softwaregrenze) in V (default: 250)")
    return p.parse_args()


def build_ramp(start: float, stop: float, step: float) -> list[float]:
    """Erzeugt eine Spannungsliste von start bis stop (inklusiv)."""
    if step <= 0:
        raise ValueError(f"Schrittweite muss positiv sein, ist aber {step}")
    voltages = []
    v = start
    direction = 1.0 if stop >= start else -1.0
    step_signed = abs(step) * direction
    while (direction > 0 and v <= stop + 1e-9) or \
          (direction < 0 and v >= stop - 1e-9):
        voltages.append(round(v, 6))
        v += step_signed
    return voltages


def delay_label(delay_s: float) -> str:
    """Menschenlesbare Bezeichnung für eine Wartezeit."""
    if delay_s <= 0.05:
        return "fast"
    elif delay_s <= 0.3:
        return "medium"
    else:
        return "slow"


# ── Hauptlogik ────────────────────────────────────────────────────────────────

def run_ramp(psu: KeysightB2901PSU, voltages: list[float], delay_s: float,
             label: str) -> list[dict]:
    """Fährt eine Rampe und gibt Soll/Ist pro Schritt aus.

    Gibt eine Liste von Ergebnis-Dicts zurück für die Zusammenfassung.
    """
    results = []
    n = len(voltages)

    print(f"\n{'='*72}")
    print(f"  Rampe: {voltages[0]:.1f} V -> {voltages[-1]:.1f} V  "
          f"({n} Schritte, delay={delay_s:.3f} s, mode={label})")
    print(f"{'='*72}")
    print(f"  {'#':>4}  {'Soll (V)':>10}  {'Ist (V)':>10}  "
          f"{'Delta (V)':>10}  {'I (A)':>12}  {'t (ms)':>8}")
    print(f"  {'----':>4}  {'--------':>10}  {'--------':>10}  "
          f"{'--------':>10}  {'----------':>12}  {'------':>8}")

    for i, v_soll in enumerate(voltages, start=1):
        # 1) Sollwert setzen
        psu.set_voltage(v_soll)

        # 2) Wartezeit (Settle)
        time.sleep(delay_s)

        # 3) Echten Istwert und Strom messen
        t0 = time.perf_counter()
        v_ist = psu.read_voltage()
        i_ist = psu.read_current()
        t_meas_ms = (time.perf_counter() - t0) * 1000

        # 4) Zur Kontrolle: auch den gesetzten Wert vom Gerät lesen
        #    :SOUR:VOLT? gibt den programmierten Wert zurück (kein Messwert!)
        v_sour = float(psu._query(":SOUR:VOLT?"))

        delta = v_ist - v_soll

        results.append({
            "step": i,
            "v_soll": v_soll,
            "v_sour": v_sour,
            "v_ist": v_ist,
            "delta": delta,
            "i_ist": i_ist,
            "t_ms": t_meas_ms,
        })

        print(f"  {i:4d}  {v_soll:10.4f}  {v_ist:10.4f}  "
              f"{delta:+10.4f}  {i_ist:12.4e}  {t_meas_ms:8.1f}")

    return results


def print_summary(all_results: dict[str, list[dict]]) -> None:
    """Druckt eine Zusammenfassung aller Rampen."""
    print(f"\n{'='*72}")
    print("  ZUSAMMENFASSUNG")
    print(f"{'='*72}")
    print(f"  {'Modus':<10}  {'|delta| max':>12}  {'|delta| mean':>12}  "
          f"{'|delta| min':>12}  {'Schritte':>8}")
    print(f"  {'-----':<10}  {'----------':>12}  {'-----------':>12}  "
          f"{'----------':>12}  {'--------':>8}")

    for label, results in all_results.items():
        deltas = [abs(r["delta"]) for r in results]
        if deltas:
            d_max = max(deltas)
            d_min = min(deltas)
            d_mean = sum(deltas) / len(deltas)
        else:
            d_max = d_min = d_mean = float("nan")

        print(f"  {label:<10}  {d_max:12.6f}  {d_mean:12.6f}  "
              f"{d_min:12.6f}  {len(results):8d}")

    # Soll vs. Source-Register-Vergleich
    print(f"\n  Hinweis: :SOUR:VOLT? = programmierter Wert (kein Messwert)")
    print(f"           :MEAS:VOLT? = echter Istwert am Ausgang")

    # Prüfe ob Source-Register und Sollwert immer identisch sind
    for label, results in all_results.items():
        mismatches = [r for r in results if abs(r["v_sour"] - r["v_soll"]) > 1e-6]
        if mismatches:
            print(f"  WARNUNG ({label}): {len(mismatches)} Punkte mit "
                  f"SOUR:VOLT? != Sollwert!")


def main() -> None:
    args = parse_args()

    # Wartezeiten parsen
    try:
        delays = [float(d.strip()) for d in args.delays.split(",")]
    except ValueError:
        print(f"FEHLER: Ungültige --delays: {args.delays!r}", file=sys.stderr)
        sys.exit(1)

    # Rampe erzeugen
    voltages = build_ramp(args.start, args.stop, args.step)
    if not voltages:
        print("FEHLER: Leere Rampe.", file=sys.stderr)
        sys.exit(1)

    # Spannungsgrenzen prüfen
    v_max = args.v_max
    for v in voltages:
        if abs(v) > v_max:
            print(f"FEHLER: Spannung {v} V überschreitet Grenze ±{v_max} V.",
                  file=sys.stderr)
            sys.exit(1)

    print(f"Keysight B2901A Rampen-Test")
    print(f"  Resource:    {args.resource}")
    print(f"  Rampe:       {args.start} -> {args.stop} V, Schritt {args.step} V")
    print(f"  Punkte:      {len(voltages)}")
    print(f"  Delays:      {delays} s")
    print(f"  Compliance:  {args.compliance} A")
    print(f"  V_max:       ±{v_max} V")

    # Gerät verbinden
    psu = KeysightB2901PSU(
        visa_resource=args.resource,
        timeout=args.timeout,
        v_min=-v_max,
        v_max=v_max,
        current_compliance=args.compliance,
    )

    try:
        print(f"\nVerbinde mit {args.resource} ...")
        idn = psu.connect()
        print(f"  IDN: {idn}")

        # Output einschalten bei 0 V
        psu.set_voltage(0.0)
        psu.output(True)
        time.sleep(0.1)
        print(f"  Output ON, Startspannung 0 V")

        # Rampen mit verschiedenen Geschwindigkeiten
        all_results: dict[str, list[dict]] = {}

        for delay_s in delays:
            label = f"{delay_label(delay_s)} ({delay_s:.3f}s)"

            # Rampe hoch
            results_up = run_ramp(psu, voltages, delay_s, label + " UP")

            # Rampe runter
            voltages_down = list(reversed(voltages))
            results_down = run_ramp(psu, voltages_down, delay_s, label + " DOWN")

            all_results[f"{label} UP"] = results_up
            all_results[f"{label} DOWN"] = results_down

            # Kurze Pause zwischen Geschwindigkeiten
            psu.set_voltage(0.0)
            time.sleep(0.5)

        print_summary(all_results)

    except KeyboardInterrupt:
        print("\n\nAbgebrochen durch Benutzer.")

    except Exception as e:
        print(f"\nFEHLER: {e}", file=sys.stderr)

    finally:
        print(f"\nSichere Abschaltung: Spannung -> 0 V, Output OFF ...")
        try:
            psu.set_voltage(0.0)
            time.sleep(0.05)
        except Exception:
            pass
        psu.close()
        print("  Fertig.")


if __name__ == "__main__":
    main()
