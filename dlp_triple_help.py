"""End-user documentation dialog for the Triple-probe analysis.

Sibling to :mod:`dlp_single_help` and :mod:`dlp_double_help`.  Opens
from the "Help…" button on the Triple-probe measurement window.

Style is deliberately shared with the Double help dialog — we reuse
the readable-help constants and ``help_style_block`` from
:mod:`dlp_double_help` so all three operator-facing help documents
look like parts of the same manual.
"""
from __future__ import annotations

from dlp_double_help import (
    HELP_BODY_FONT_SIZE_PT,
    HELP_FONT_FAMILY,
    HELP_FORMULA_FONT_SIZE_PT,
    HELP_LINE_HEIGHT,
    help_style_block,
)


HELP_BODY = """
<h2>Triple-probe analysis &mdash; at a glance</h2>
<p>
  The Triple-Langmuir probe has three identical tips immersed in the
  plasma.  One tip is actively biased by the SMU to a positive
  voltage <span class="term">V<sub>d12</sub></span> relative to the
  second tip; the third tip floats.  Reading the floating potential
  difference <span class="term">V<sub>d13</sub></span> with a high-
  impedance DMM (the K2000) while the biased loop is held fixed
  gives <span class="term">T<sub>e</sub></span> directly and
  instantaneously &mdash; no voltage sweep, no fit, one sample per
  tick.
</p>
<p>
  This method is the tool of choice for <em>fast transients</em> and
  <em>live monitoring</em>: every tick produces a fresh estimate,
  and the probe does not need to scan through V_f or V_p.
</p>

<h3>The equations shipped in this application</h3>
<p>There are two solver paths, selectable in the operator UI:</p>
<span class="formula">
  Approx.:&nbsp;&nbsp;T<sub>e</sub>&nbsp;=&nbsp;V<sub>d13</sub>&nbsp;/&nbsp;ln 2
</span>
<p>
  The closed-form approximation used by Chen and others: under a
  Maxwellian-electrons and equal-area assumption, the floating
  third tip sits halfway between V_f of probe 2 and V_f of probe 1,
  which yields V_d13 = T_e · ln 2.  Fast, robust, no iteration.
</p>
<span class="formula">
  Exact:&nbsp;&nbsp;
  exp(&minus;V<sub>d13</sub>/T<sub>e</sub>)&nbsp;&minus;&nbsp;2&middot;exp(&minus;V<sub>d12</sub>/T<sub>e</sub>)&nbsp;+&nbsp;1&nbsp;=&nbsp;0
</span>
<p>
  The implicit triple-probe equation (Eq. 10 in the original
  derivations).  Solved numerically by bisection.  Falls back to
  the closed-form above when the bisection cannot bracket a root,
  so the live display never stalls on a pathological tick.
</p>

<h3>How n<sub>e</sub> is obtained</h3>
<p>
  Once <span class="term">T<sub>e</sub></span> is known, the ion-
  saturation current on probe 2 (directly measured by the SMU) is
  turned into a density via the Bohm flux:
</p>
<span class="formula">
  v<sub>Bohm</sub>&nbsp;=&nbsp;&radic;(k<sub>B</sub>&middot;T<sub>e</sub>&nbsp;/&nbsp;m<sub>i</sub>)
</span>
<span class="formula">
  n<sub>e</sub>&nbsp;=&nbsp;I<sub>sat</sub>&nbsp;/&nbsp;(e&nbsp;&middot;&nbsp;A&nbsp;&middot;&nbsp;v<sub>Bohm</sub>)
</span>
<p>
  Here <span class="term">A</span> is the probe collection area and
  <span class="term">m<sub>i</sub></span> is the ion mass of the
  working gas.  The gas mix comes from the main window's
  <em>Experiment…</em> dialog; the probe area comes from the main
  window's <em>Probe Params…</em> dialog.  The Triple window only
  <em>mirrors</em> these values — edit them via the main window.
</p>

<h2>Assumptions you are implicitly signing up for</h2>
<ul>
  <li>All three tips have <span class="term">identical</span>
      collection area and work function.  A large asymmetry (tip
      damage, contamination on one tip) voids T_e = V_d13 / ln 2.</li>
  <li>The electron distribution is <span class="term">Maxwellian</span>
      over the potentials seen by the three tips.  Non-Maxwellian
      tails (RF beams, secondaries) inflate T_e.</li>
  <li>The probe operates in <span class="term">ion-saturation</span>
      on probe 2 at the chosen V_d12.  If V_d12 is too small the
      biased tip is still in the electron branch and the derived
      T_e is meaningless.</li>
  <li><span class="term">V<sub>d13</sub></span> is read with a
      <em>high-impedance</em> DMM (the K2000).  A low-impedance
      meter loads the floating tip and biases the result toward
      zero.</li>
  <li>The sign convention you select in the
      <em>V<sub>d13</sub> sign</em> combo must match your wiring:
      <span class="term">+1</span> if your K2000 reads
      (V<sub>probe3</sub> &minus; V<sub>probe1</sub>) positive,
      <span class="term">-1</span> otherwise.  The wrong sign is
      the single most common cause of negative T_e.</li>
</ul>

<h2>The parameters in the Triple window</h2>
<table class="opts">
  <tr>
    <td class="k">V<sub>d12</sub> (bias)</td>
    <td>The SMU bias between probe 1 and probe 2.  Must be large
        enough that probe 2 is in ion saturation — typical 15–30 V
        for low-density labs; large enough that
        exp(&minus;V<sub>d12</sub>/T<sub>e</sub>) &laquo; 1.</td>
  </tr>
  <tr>
    <td class="k">Compliance</td>
    <td>SMU current limit.  Keep comfortably above the expected
        ion-saturation current on probe 2.  The live compliance LED
        turns red when the limit is hit — a clipped sample
        invalidates that tick's T_e.</td>
  </tr>
  <tr>
    <td class="k">Probe area / Gas mix</td>
    <td>Read-only here.  Change via the main window's
        <em>Probe Params…</em> and <em>Experiment…</em> dialogs.
        Both are only needed for the density calculation, not for
        T_e itself.</td>
  </tr>
  <tr>
    <td class="k">V<sub>d13</sub> sign</td>
    <td>The polarity of the K2000 reading relative to
        (V<sub>probe3</sub> &minus; V<sub>probe1</sub>).  Wrong sign
        &rarr; negative T_e.  Verify once on bench start-up.</td>
  </tr>
  <tr>
    <td class="k">Formula</td>
    <td><em>Approx.</em> (closed-form) or <em>Exact</em> (implicit
        equation solved by bisection).  Approx. is the safe
        default; Exact matters only when V<sub>d12</sub> is not
        comfortably above a few T<sub>e</sub>.</td>
  </tr>
  <tr>
    <td class="k">Tick</td>
    <td>Sample interval in milliseconds.  50–200 ms is typical.
        Lower ticks stress the SMU (it must settle after each
        bias) and the K2000 NPLC must be set accordingly — see
        <em>K2000 Options…</em> in the main window.</td>
  </tr>
</table>

<h2>Reading the live readout</h2>
<table class="opts">
  <tr>
    <td class="k">U<sub>K2000</sub></td>
    <td>Last V<sub>d13</sub> sample from the DMM, signed according
        to the selected sign convention.  If this flips sign during
        a run, either the plasma condition changed or the wiring
        lost contact.</td>
  </tr>
  <tr>
    <td class="k">I<sub>SMU</sub></td>
    <td>The probe-2 current measured by the SMU.  Proportional to
        n<sub>e</sub> via the Bohm flux once T<sub>e</sub> is
        known.</td>
  </tr>
  <tr>
    <td class="k">T<sub>e</sub></td>
    <td>The per-tick electron temperature.  On a stable plasma the
        Approx. and Exact values agree within a percent or two; a
        sudden divergence between the two is a sign of drifting
        plasma conditions.</td>
  </tr>
  <tr>
    <td class="k">n<sub>e</sub></td>
    <td>Electron density derived from I<sub>sat</sub>, T<sub>e</sub>,
        probe area and ion mass.  Assumes Bohm flux and equal
        tip areas.</td>
  </tr>
</table>

<h2>Saving and limitations</h2>
<p>
  Triple data is appended to a dedicated
  <span class="term">&lt;base&gt;/triple/</span> CSV via the
  project's versioned schema banner (see the CSV schema note in
  the main documentation).  One row per tick.
</p>
<p class="warn">
  Triple does NOT use the Double compliance-exclusion logic — each
  tick is independent and simply flagged if the compliance LED was
  red during that tick.  A sustained compliance event means the
  SMU cannot source the bias: re-check probe wiring or increase
  the compliance limit.
</p>
<p class="note">
  If T<sub>e</sub> is negative or unphysically large for more than
  a few ticks, the first three things to check (in order):
</p>
<ol>
  <li>V<sub>d13</sub> sign.</li>
  <li>V<sub>d12</sub> below a few T<sub>e</sub> &rarr; probe 2 not
      in ion saturation.</li>
  <li>K2000 compliance / connection — a floating cable reads as
      noise and the bisection fails on noise.</li>
</ol>
"""


def HELP_HTML() -> str:
    """Full Triple-probe help HTML, assembled at call time so a
    style edit (in :func:`dlp_double_help.help_style_block`) is
    picked up without stale caches."""
    return help_style_block() + HELP_BODY


# ---------------------------------------------------------------------------
# Qt dialog
# ---------------------------------------------------------------------------
def open_triple_help_dialog(parent=None) -> None:
    """Modal helper: build, show, dispose of the Triple-probe help
    window.  Safe to call repeatedly."""
    dlg = TripleAnalysisHelpDialog(parent=parent)
    dlg.exec()


class TripleAnalysisHelpDialog:
    """Read-only help window for the Triple-probe analysis.

    Renders :func:`HELP_HTML` inside a scrollable QTextBrowser,
    sized for comfortable documentation reading.  Qt imports lazy
    so this module stays import-cheap for headless tests.
    """

    DEFAULT_SIZE = (820, 680)

    def __init__(self, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox)
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle(
            "Triple-probe analysis \u2014 help")
        self._dlg.setMinimumSize(*self.DEFAULT_SIZE)
        self._dlg.resize(*self.DEFAULT_SIZE)

        layout = QVBoxLayout(self._dlg)
        layout.setContentsMargins(10, 10, 10, 10)

        self.txt = QTextBrowser(self._dlg)
        self.txt.setOpenExternalLinks(False)
        self.txt.setStyleSheet(
            "QTextBrowser {"
            "  background-color: #1e2126;"
            "  color: #e6e6e6;"
            "  selection-background-color: #2a6a96;"
            "  selection-color: #ffffff;"
            "  border: 1px solid #38404a;"
            "}"
        )
        self.txt.document().setDefaultStyleSheet(
            f"body {{ color:#e6e6e6; background-color:#1e2126; "
            f"font-size:{HELP_BODY_FONT_SIZE_PT}pt; }} "
            ".formula { background:#262a30; color:#ffe9a8; "
            "  border-left:3px solid #7fc8ff; padding:7px 11px; } "
            ".term, td.k { color:#9adcff; font-weight:bold; } "
            ".note { color:#f0c060; } "
            ".warn { color:#f06060; } "
            "h2, h3 { color:#7fc8ff; }"
        )
        self.txt.setHtml(HELP_HTML())
        layout.addWidget(self.txt, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self._dlg.reject)
        btns.accepted.connect(self._dlg.accept)
        for btn in btns.buttons():
            btn.clicked.connect(self._dlg.accept)
        layout.addWidget(btns)

    def exec(self):
        return self._dlg.exec()


__all__ = [
    "HELP_BODY", "HELP_HTML",
    "open_triple_help_dialog", "TripleAnalysisHelpDialog",
]
