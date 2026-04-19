"""End-user help dialog for the Experiment Parameters dialog.

Explains the gas-mix + ion-composition controls in plain language:
what the gas rows mean, what "ion composition" does, how inert vs
molecular gases are treated differently, and how the per-gas
settings combine into the effective ion mass used by every probe
method.

Open via the "Help" button on
:class:`dlp_experiment_dialog.ExperimentParameterDialog` or the
convenience helper :func:`open_experiment_help_dialog`.

Visual style mirrors ``dlp_single_help`` / ``dlp_double_help``:
dark palette, Qt-HTML rich text, imports Qt lazily so this module
is import-cheap for headless tests.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# HTML body.  Exposed as a module-level constant so headless tests
# can assert content without instantiating Qt.
# ---------------------------------------------------------------------------
HELP_HTML = """
<style>
  body {
    font-family: 'Segoe UI', 'Helvetica Neue', Helvetica, Arial,
                  'DejaVu Sans', sans-serif;
    font-size: 13pt;
    line-height: 1.55;
    color: #e6e6e6;
    background-color: #1e2126;
  }
  p, li, td { color: #e6e6e6; line-height: 1.55; }
  h2 {
    color: #7fc8ff;
    font-size: 17pt;
    margin-top: 18px;
    margin-bottom: 4px;
    border-bottom: 1px solid #38404a;
    padding-bottom: 3px;
  }
  h3 {
    color: #7fc8ff;
    font-size: 15pt;
    margin-top: 14px;
    margin-bottom: 3px;
  }
  .formula {
    font-family: Consolas, 'Cascadia Code', 'Courier New', monospace;
    font-size: 13pt;
    background: #262a30;
    color: #ffe9a8;
    border-left: 3px solid #7fc8ff;
    padding: 7px 11px;
    margin: 7px 0;
    display: block;
  }
  .term { color: #9adcff; font-weight: bold; }
  .note { color: #f0c060; }
  table.opts { border-collapse: collapse; margin: 6px 0; }
  table.opts td {
    vertical-align: top;
    padding: 3px 10px 3px 0;
    color: #e6e6e6;
  }
  table.opts td.k { color: #9adcff; font-weight: bold; white-space: nowrap; }
</style>

<h2>What this dialog configures</h2>
<p>The Experiment Parameters dialog captures two physically
distinct things so the Bohm density formula used by Single,
Double, and Triple analyses has a defensible ion-mass
assumption:</p>
<ul>
  <li><span class="term">Feed gas composition</span> — what
  you <em>let into the chamber at the inlet</em>, in sccm.
  Molecular feed gases (O<sub>2</sub>, N<sub>2</sub>,
  H<sub>2</sub>) are always entered as molecular feed flows.
  The sccm→mg/s conversion and the mean feed molar mass both use
  the <em>neutral molecular</em> molar mass.</li>
  <li><span class="term">Plasma-phase ion composition</span>
  — what positive ion you assume dominates at the probe
  <em>sheath</em> inside the plasma.  This is a state-of-the-
  plasma property, not a statement about the feed.  For
  molecular feed gases you choose whether the positive ion is
  the intact molecule, a dissociated atom, or a mix — and how
  confident you are.</li>
</ul>
<p class="note">The two concepts never cross: changing a plasma-
phase assumption does <b>not</b> rescale the feed flow, and
entering more sccm does not change the plasma's dissociation
fraction.</p>

<h2>Gas rows</h2>
<p>Each row is a gas species plus a volumetric flow.  The dialog
converts sccm to mg/s next to each row so you can cross-check
against a mass-flow controller.  Leave a row as <code>(none)</code>
if you use fewer than three gases.  Rows with zero flow or no
species are ignored everywhere downstream.</p>
<p>The effective ion mass used in the Bohm formula is the
<span class="term">flow-weighted mean</span> of every active
row's positive-ion mass:</p>
<div class="formula">
m_i = (&Sigma;<sub>gas</sub> &nbsp;f<sub>gas</sub> &middot;
m<sub>ion,gas</sub>) &nbsp;/&nbsp; &Sigma;<sub>gas</sub>
&nbsp;f<sub>gas</sub>
</div>

<h2>Inert vs molecular gases</h2>
<p>Noble / monatomic gases (<span class="term">Ar, He, Ne, Kr,
Xe</span>) are always treated as single-atom positive ions —
there is no molecular↔atomic ambiguity.  Their ion-composition
editor is disabled and shows an inert-gas note.  They still
contribute to the flow-weighted mean above — their presence
always shifts m<sub>i</sub>.</p>
<p>Molecular gases (<span class="term">O<sub>2</sub>,
N<sub>2</sub>, H<sub>2</sub></span>) can dissociate in the
plasma.  Which positive ion dominates depends on the discharge
regime, which is why each molecular gas carries its own
ion-composition editor.</p>

<h2>Ion-composition modes</h2>
<table class="opts">
  <tr><td class="k">Molecular</td>
      <td>The positive ion is the intact molecule
          (O<sub>2</sub><sup>+</sup>, N<sub>2</sub><sup>+</sup>,
          H<sub>2</sub><sup>+</sup>).  No uncertainty from
          composition.  Typical for magnetron / moderate-pressure
          DC or RF discharges.</td></tr>
  <tr><td class="k">Atomic</td>
      <td>The positive ion is the dissociated atom
          (O<sup>+</sup>, N<sup>+</sup>, H<sup>+</sup>).  No
          uncertainty either — you claim to know.  Typical for
          low-pressure high-density ICP / ECR sources.</td></tr>
  <tr><td class="k">Mixed (x &plusmn; &Delta;x)</td>
      <td>You have a best estimate for the atomic-ion
          fraction <span class="term">x</span> and its half-width
          uncertainty <span class="term">&Delta;x</span>.  The
          effective per-gas mass becomes
          <code>(1&minus;x)&middot;m<sub>mol</sub> +
          x&middot;m<sub>atomic</sub></code>; the CI widens with
          |m<sub>mol</sub>&minus;m<sub>atomic</sub>|&middot;&Delta;x.</td></tr>
  <tr><td class="k">Unknown</td>
      <td>You do not know.  The per-gas mass collapses to the
          mid-point and the CI spans the full molecular↔atomic
          bracket.  Use this when you cannot defend a narrower
          choice.</td></tr>
</table>

<h2>Feed gas vs plasma ion composition</h2>
<p>This is the single most important distinction in this
dialog:</p>
<ul>
  <li><span class="term">Feed gas = inlet identity.</span>
      Whatever you selected in the top group stays what you
      fed.  <em>Entering 1&nbsp;sccm of N<sub>2</sub> means
      1&nbsp;sccm of N<sub>2</sub> molecules at the inlet,
      always</em>.  The mg/s column is computed from the
      neutral molecular molar mass; it does <b>not</b> depend
      on any ion-composition choice below.</li>
  <li><span class="term">Plasma ion composition = sheath-side
      assumption.</span>  When you change the ion-composition
      mode for a molecular gas (for example N<sub>2</sub> to
      <em>atomic</em>), you are saying "inside the plasma the
      dominant positive ion arriving at my probe's sheath is
      N<sup>+</sup> rather than N<sub>2</sub><sup>+</sup>".
      The Bohm formula uses this new ion mass to infer
      n<sub>i</sub> from the measured ion saturation
      current.</li>
</ul>
<p><strong>Worked example: N<sub>2</sub> &rarr; 2N.</strong>
Cold N<sub>2</sub> plasma at modest power: little dissociation,
N<sub>2</sub><sup>+</sup> dominates, choose <em>molecular</em>
(m = 28&nbsp;u).  Same feed but high-power ICP: substantial
N<sub>2</sub>&nbsp;&rarr;&nbsp;2N dissociation, N<sup>+</sup> can
dominate, choose <em>atomic</em> (m = 14&nbsp;u).  <b>Either
choice leaves the feed flow unchanged</b> — you still fed
N<sub>2</sub> at the inlet.  The mg/s column still quotes the
molecular flow rate.  Only the ion mass going into the Bohm
formula shifts.  If you are unsure which regime you are in,
pick <em>mixed</em> (x&nbsp;&plusmn;&nbsp;&Delta;x) or
<em>unknown</em> — both widen the reported CI honestly rather
than committing to one.</p>
<p class="note">Selecting <em>atomic</em> never means "I am
feeding atomic nitrogen at the inlet".  That would require a
separate atomic-source gas line which this dialog does not
model.</p>

<h2>How the mixture mass is computed</h2>
<p>Internally the software approximates the effective sheath-side
ion mass as a <span class="term">feed-flow-weighted arithmetic
mean</span> of the per-gas ion-mass assumptions:</p>
<div class="formula">
m<sub>i,eff</sub> = &Sigma;<sub>g</sub>&nbsp;
f<sub>g</sub>&nbsp;&middot;&nbsp;m<sub>ion,g</sub> &nbsp;/&nbsp;
&Sigma;<sub>g</sub>&nbsp;f<sub>g</sub>
</div>
<p>This is a pragmatic first-order approximation widely used in
the low-temperature probe community when a full plasma-chemistry
model is not available.  Two caveats to keep in mind:</p>
<ul>
  <li><span class="term">Feed-flow ratios are not
      ion-density ratios.</span>  True ion-density ratios in the
      plasma depend on ionisation rates, residence time, and
      discharge power — not on feed ratios alone.  The software
      uses feed-flow ratios as a proxy because they are the
      only quantity available at the inlet.</li>
  <li><span class="term">The rigorous multi-ion Bohm
      reduction is harmonic-like, not arithmetic.</span>
      Summing partial fluxes
      <code>&Sigma;<sub>s</sub>&nbsp;n<sub>s</sub>/&radic;m<sub>s</sub></code>
      and matching to a single-species Bohm form yields an
      effective mass of the form
      <code>1&nbsp;/&nbsp;[&Sigma;<sub>s</sub>&nbsp;(n<sub>s</sub>/n<sub>tot</sub>)/&radic;m<sub>s</sub>]<sup>2</sup></code>.
      For mixed-mass plasmas (e.g. O<sub>2</sub>&nbsp;+&nbsp;Xe)
      the arithmetic and harmonic forms can differ by tens of
      per-cent; the software uses the arithmetic form
      conservatively and documents it here.</li>
</ul>
<p>If this level of approximation is unacceptable for your
measurement, fix the ion composition per gas explicitly
(<em>molecular</em> or <em>atomic</em>) rather than letting a
mixed-mode default apply, and report the assumption alongside
the density.</p>

<h2>How x &plusmn; &Delta;x affects density uncertainty</h2>
<p>The Bohm velocity v<sub>Bohm</sub> scales as &radic;(T<sub>e</sub>/m<sub>i</sub>),
so density scales as
<code>n &prop; 1&nbsp;/&nbsp;&radic;m<sub>i</sub></code>.  A
relative mass uncertainty &sigma;<sub>m</sub>/m becomes a relative
density uncertainty of <code>&frac12; &middot; &sigma;<sub>m</sub>/m</code>.
That is why halving your ion-mass uncertainty only halves the
density uncertainty the analysis reports — the scaling is mild
but real.</p>
<p>For Double, Triple, and Single analyses, the ion-composition
uncertainty is folded into the reported <span class="term">n<sub>i</sub></span>
/ <span class="term">n<sub>e</sub></span> confidence intervals
(scope tag: <code>ion_mix</code>).  The CSV header + sidecar
record the exact assumption so a later reader can tell what you
asked for.</p>

<h2>Worked example: O<sub>2</sub> + Xe</h2>
<p>Feed: 0.1&nbsp;sccm O<sub>2</sub> + 0.1&nbsp;sccm Xe at the
inlet.  Both gases contribute half the feed flow and therefore
half the flow-weight, no matter which ion-composition mode
you pick for O<sub>2</sub>:</p>
<ul>
  <li>Xe is inert — its ion is Xe<sup>+</sup> at 131.3&nbsp;u,
      no ambiguity.  The "per-gas composition" control is
      disabled for Xe.</li>
  <li>O<sub>2</sub> is molecular.  The Xe + O<sub>2</sub> plasma
      may or may not dissociate O<sub>2</sub>; set
      the O<sub>2</sub> row's mode to <code>molecular</code>
      (m = 32&nbsp;u), <code>atomic</code> (m = 16&nbsp;u),
      <code>mixed</code> (m = 32 − 16&middot;x&nbsp;u), or
      <code>unknown</code> (m = 24&nbsp;u &plusmn; 8&nbsp;u).</li>
  <li>The effective mixture mass is then
      <code>0.5&middot;m<sub>O<sub>2</sub>-ion</sub>
      + 0.5&middot;m<sub>Xe-ion</sub></code>.  Xe is always
      included in this sum — changing the O<sub>2</sub> mode
      only changes the O<sub>2</sub> term.</li>
</ul>
<p class="note">That is the key point of the per-gas design:
<span class="term">only O<sub>2</sub>'s mode affects the
O<sub>2</sub> contribution</span>.  Xe and every other inert gas
are still part of the calculation — they just aren't affected by
molecular/atomic knobs that do not apply to them.</p>

<h2>Default (fallback) vs per-gas</h2>
<p>At the bottom of the Ion Composition section is a
<span class="term">"default" block</span>.  Any molecular gas
whose own row does not override the composition inherits this
default.  If you only have one molecular gas, setting the
default is equivalent to setting the per-gas editor for that
row.  When multiple molecular gases are present, prefer the
per-gas editors so the assumption is unambiguous for each.</p>

<h2>Presets</h2>
<p>Each per-gas row offers presets that apply only to that gas
(for example, O<sub>2</sub>-magnetron-molecular and
O<sub>2</sub>-high-power-ICP are offered on the O<sub>2</sub>
row, not on N<sub>2</sub> or H<sub>2</sub>).  A preset fills the
mode / x / &Delta;x fields with conservative starting values for
that regime.  Manually editing any field afterwards flips the
preset back to <em>Custom</em>, so the UI never lies about which
preset is actually in effect.</p>
<p>The default block below the per-gas editors has its own
gas-agnostic preset selector — useful when a single molecular
gas dominates the mixture or when you want every molecular gas
to start from the same assumption.</p>

<h2>Persistence</h2>
<p>The dialog writes both the legacy global triple
(<code>ion_composition_mode</code> / <code>x_atomic</code> /
<code>x_atomic_unc</code> / <code>ion_composition_preset</code>)
and the new <code>per_gas_composition</code> dict to the
sidecar + CSV headers.  Old files without the dict load cleanly
— the analysis falls back to the global triple.  New files can
be re-opened in older builds; the per-gas overrides are simply
ignored.</p>

<h2>FAQ</h2>
<p><strong>Why does the default still exist?</strong> &mdash; For
single-molecular-gas workflows it is the simplest control.  When
more than one molecular gas is present, use the per-gas editors
instead.</p>
<p><strong>Does Xe get the "O<sub>2</sub> high-power ICP"
preset when I pick it in the default?</strong> &mdash; No.  Xe
is monatomic; the calculation silently collapses to Xe<sup>+</sup>
at 131.3&nbsp;u regardless of any molecular-oriented preset.  The
per-gas UI makes that explicit by disabling Xe's editor.</p>
<p><strong>Does changing ion composition change T<sub>e</sub>?</strong>
&mdash; No.  Only m<sub>i</sub> and therefore n<sub>i</sub> /
n<sub>e</sub> via the Bohm flux.  T<sub>e</sub> comes from the
fitted I–V characteristic (Single / Double) or the algebraic
triple-probe solve (Triple).</p>
"""


def open_experiment_help_dialog(parent=None) -> None:
    """Modal helper: build, show, and clean up the Experiment Parameters
    help window.  Safe to call repeatedly."""
    dlg = ExperimentHelpDialog(parent=parent)
    dlg.exec()


class ExperimentHelpDialog:
    """Read-only help window for the Experiment Parameters dialog.

    Renders :data:`HELP_HTML` inside a scrollable
    :class:`QTextBrowser` sized large enough that the typical
    operator does not need to resize on first open.  Imports Qt
    lazily so this module stays import-cheap for headless tests.
    """

    DEFAULT_SIZE = (760, 640)

    def __init__(self, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox)
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle("Experiment parameters \u2014 help")
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
            "body { color:#e6e6e6; background-color:#1e2126; "
            "  font-size:13pt; } "
            ".formula { background:#262a30; color:#ffe9a8; "
            "  border-left:3px solid #7fc8ff; padding:7px 11px; } "
            ".term, td.k { color:#9adcff; font-weight:bold; } "
            ".note { color:#f0c060; } "
            "h2, h3 { color:#7fc8ff; }"
        )
        self.txt.setHtml(HELP_HTML)
        layout.addWidget(self.txt, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self._dlg.reject)
        btns.accepted.connect(self._dlg.accept)
        for btn in btns.buttons():
            btn.clicked.connect(self._dlg.accept)
        layout.addWidget(btns)

    def exec(self):
        return self._dlg.exec()


__all__ = [
    "HELP_HTML",
    "ExperimentHelpDialog",
    "open_experiment_help_dialog",
]
