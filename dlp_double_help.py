"""End-user documentation dialog for the Double-probe analysis.

Sibling to :mod:`dlp_single_help`.  Opens as a modal window from the
"Help" button on :class:`dlp_double_options.DoubleAnalysisOptionsDialog`
or via :func:`open_double_help_dialog`.

Content is curated for lab operators (not developers): every option
and every caveat surfaced in the Double-probe analysis pipeline is
explained with the formula and the assumption it rests on.  Rendered
as Qt HTML rich text — no external dependencies, no network access.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Shared readable-help styling.  Importing this constant keeps the two
# help modules (Single + Double) visually consistent and lets future
# polish touch both at once.  The font stack is chosen for reliability:
# every entry is either pre-installed on supported Windows lab PCs or
# bundled with PySide6 / the OS (Segoe UI, Arial), or falls back
# cleanly to Qt's sans-serif proxy.
# ---------------------------------------------------------------------------
HELP_FONT_FAMILY = (
    "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, "
    "'DejaVu Sans', sans-serif"
)
HELP_BODY_FONT_SIZE_PT = 13      # documentation-friendly body size
HELP_LINE_HEIGHT = "1.55"        # extra line spacing for prose readability
HELP_FORMULA_FONT_SIZE_PT = 13   # formulas should not be smaller than body


def help_style_block() -> str:
    """Return the shared ``<style>`` block used by the help dialogs.

    Kept as a function so downstream modules can embed it into their
    own HTML without duplicating the constants.
    """
    return f"""
<style>
  body {{
    font-family: {HELP_FONT_FAMILY};
    font-size: {HELP_BODY_FONT_SIZE_PT}pt;
    line-height: {HELP_LINE_HEIGHT};
    color: #e6e6e6;
    background-color: #1e2126;
  }}
  p, li, td {{ color: #e6e6e6; line-height: {HELP_LINE_HEIGHT}; }}
  h2 {{
    color: #7fc8ff;
    font-size: {HELP_BODY_FONT_SIZE_PT + 4}pt;
    margin-top: 18px;
    margin-bottom: 4px;
    border-bottom: 1px solid #38404a;
    padding-bottom: 3px;
  }}
  h3 {{
    color: #7fc8ff;
    font-size: {HELP_BODY_FONT_SIZE_PT + 2}pt;
    margin-top: 14px;
    margin-bottom: 3px;
  }}
  .formula {{
    font-family: Consolas, 'Cascadia Code', 'Courier New', monospace;
    font-size: {HELP_FORMULA_FONT_SIZE_PT}pt;
    background: #262a30;
    color: #ffe9a8;
    border-left: 3px solid #7fc8ff;
    padding: 7px 11px;
    margin: 7px 0;
    display: block;
  }}
  .term {{ color: #9adcff; font-weight: bold; }}
  .note {{ color: #f0c060; }}
  .warn {{ color: #f06060; }}
  table.opts {{ border-collapse: collapse; margin: 6px 0; }}
  table.opts td {{
    vertical-align: top;
    padding: 4px 10px;
    color: #e6e6e6;
  }}
  table.opts td.k {{
    color: #9adcff;
    font-weight: bold;
    white-space: nowrap;
  }}
</style>
"""


HELP_BODY = """
<h2>Double-probe analysis &mdash; at a glance</h2>
<p>
  The Double-Langmuir probe sees <em>two identical tips</em> immersed in the
  plasma and held at a swept bias between them.  Because the whole
  pair floats relative to the plasma, the method does not require a
  probe-to-wall reference and is relatively robust in RF /
  magnetised discharges where a Single probe is hard to ground.
</p>

<h3>The fitted equation</h3>
<p>The shipping tanh-family models describe the symmetric double-probe
  I&ndash;V curve as:</p>
<span class="formula">
  I(V) &nbsp;=&nbsp; I<sub>sat</sub> &middot; tanh(V / W)
  &nbsp;&nbsp;[+ g&middot;V]&nbsp;&nbsp;[&middot; (1 + a&middot;tanh(V/W))]
</span>
<p>
  <span class="term">W</span> is the fit's width parameter. Under the
  Maxwellian-electrons assumption <span class="term">T<sub>e</sub>&nbsp;=&nbsp;W&nbsp;/&nbsp;2</span>
  (in electron-volts, when V is in volts).  <span class="term">I<sub>sat</sub></span>
  is the ion saturation current magnitude on each probe.
</p>

<h2>Fit model &mdash; which variant to pick?</h2>
<table class="opts">
  <tr>
    <td class="k">Simple tanh</td>
    <td>Two parameters (<span class="term">I<sub>sat</sub></span>,
        <span class="term">W</span>).  Use when the saturation branches
        look very flat and the sheath-conductance term is negligible.
        Fits the <em>sheath-corrected</em> current (the saturation
        linear slopes are subtracted first).</td>
  </tr>
  <tr>
    <td class="k">tanh + slope</td>
    <td>Adds an explicit linear term <span class="term">g&middot;V</span>
        on the raw data &mdash; the default choice.  Handles residual sheath
        conductance / probe-area growth without a separate correction step.</td>
  </tr>
  <tr>
    <td class="k">tanh + slope + asymmetry</td>
    <td>Adds an <span class="term">a</span> term that lets the two saturation
        levels differ.  Reach for this only when the two probe tips are
        measurably dissimilar; otherwise the extra parameter inflates
        the covariance of <span class="term">T<sub>e</sub></span>.</td>
  </tr>
</table>

<h2>Assumptions you are implicitly signing up for</h2>
<ul>
  <li>Both probe tips have <span class="term">identical</span> collection
      area and work function.  A large asymmetry voids <span class="term">T<sub>e</sub>&nbsp;=&nbsp;W/2</span>.</li>
  <li>The electron distribution is <span class="term">Maxwellian</span>
      over the fitted voltage range.  Non-Maxwellian tails (beams,
      secondaries, RF modulation) bias T<sub>e</sub> high.</li>
  <li>The sweep is <span class="term">quasi-static</span>: settle time
      per step &gg; probe RC time.  Fast-sweep artefacts show up as
      hysteresis &mdash; see the Hysteresis warn option.</li>
  <li>The probes operate in the <span class="term">ion-saturation
      regime</span>; no secondary emission, no strongly-negative ions
      dominating the current.</li>
</ul>

<h2>The options in this dialog</h2>
<h3>Compliance</h3>
<table class="opts">
  <tr>
    <td class="k">Exclude clipped (default)</td>
    <td>Drops any sample that hit the SMU current limit
        before fitting.  Excluded points never bias
        <span class="term">I<sub>sat</sub></span>.
        The fit result carries a <span class="term">Compliance</span>
        row with the count and fraction excluded.</td>
  </tr>
  <tr>
    <td class="k">Include all (legacy)</td>
    <td>Fit every acquired point, including clipped plateaus.
        Preserves byte-for-byte comparison with pre-2026 datasets.
        Triggers an explicit <span class="warn">Fit warning</span>
        when &ge;&nbsp;10&nbsp;% of the sweep is clipped.</td>
  </tr>
</table>
<p>For legacy datasets saved without a compliance column, a
  conservative <em>plateau heuristic</em> looks for bit-flat runs
  near the sweep edges and labels them
  <span class="note">suspected clipping &mdash; (legacy heuristic)</span>.
  The reported numbers are always marked suspected, never confirmed.</p>

<h3>Hysteresis warn</h3>
<p>
  Percentage of <span class="term">|I|<sub>max</sub></span> by which
  the forward and reverse sweep branches may diverge before an
  operator-visible <span class="note">plasma-drift</span> warning is
  logged.  Real drift &mdash; gas flow settling, RF power ramp, probe
  contamination &mdash; shows up here before it shows up in T<sub>e</sub>.
</p>

<h3>Uncertainty &mdash; bootstrap CI</h3>
<p>
  The fit always reports a 95&thinsp;% covariance-based CI for
  <span class="term">T<sub>e</sub></span>, <span class="term">I<sub>sat</sub></span>,
  and <span class="term">n<sub>i</sub></span> (fit-only scope:
  probe area and ion mass are treated as exact).
  Enabling the bootstrap adds a non-parametric residual-resampling
  CI that is sharper when residuals are skewed.
</p>
<p>
  It costs roughly one extra second per analyze click (at the
  default 200 iterations).  The iterations spinner is clamped to
  <span class="term">[50, 2000]</span> so an accidental keyboard slip
  cannot freeze the GUI.
</p>

<h3>Show analysis log</h3>
<p>
  Toggles the separate <em>Analysis Log</em> window.  Off by default
  &mdash; the compact HTML summary in the main log is usually enough
  for live operation.  The persistent history file
  (<span class="term">analysis_history.txt</span>) is always written
  regardless of this flag, so you can audit previous analyses from
  disk at any time.
</p>

<h2>Reading the result block</h2>
<table class="opts">
  <tr>
    <td class="k">Status</td>
    <td>OK &mdash; fit converged and was not clipping-contaminated. POOR
        &mdash; fit ran but the grade or clipping severity mean the numbers
        should not be quoted without review.  Failure statuses
        (non_converged, bounds_error, numerical_error, insufficient_data,
        bad_input) mean the numbers are <span class="warn">not
        trustworthy</span>.</td>
  </tr>
  <tr>
    <td class="k">T<sub>e</sub></td>
    <td>Reported as <span class="term">value &plusmn; 1&sigma;</span>
        with a sub-line giving the 95&thinsp;% CI.  The CI label says
        <em>bootstrap</em> when that path was enabled and succeeded,
        otherwise <em>95&thinsp;% CI (&plusmn;z&middot;&sigma;)</em>
        &mdash; the asymptotic covariance interval.</td>
  </tr>
  <tr>
    <td class="k">I<sub>sat</sub></td>
    <td>Ion saturation current magnitude. A CI sub-line from the fit
        covariance is shown in mA.</td>
  </tr>
  <tr>
    <td class="k">n<sub>i</sub></td>
    <td>Bohm-flux density:
        <span class="term">n<sub>i</sub> = I<sub>sat</sub> / (e &middot; A &middot; v<sub>Bohm</sub>)</span>
        with <span class="term">v<sub>Bohm</sub> = &radic;(k<sub>B</sub>T<sub>e</sub> / m<sub>i</sub>)</span>.
        The CI is labelled <em>fit-only</em>: probe area and ion mass
        are treated as exact inputs, so the true density uncertainty
        is wider than this number.  See Experiment... to configure
        gas composition.</td>
  </tr>
  <tr>
    <td class="k">Fit [grade]</td>
    <td><em>excellent</em>: R&sup2; &ge; 0.999 <b>and</b>
        NRMSE &le; 1&thinsp;%. <em>good</em>: R&sup2; &ge; 0.99 and
        NRMSE &le; 5&thinsp;%. <em>fair</em>: R&sup2; &ge; 0.95 and
        NRMSE &le; 10&thinsp;%.  <em>poor</em>: anything else
        &mdash; the fit ran but the shape does not match the model.</td>
  </tr>
  <tr>
    <td class="k">Compliance</td>
    <td>Only shown when clipping was observed.  N/M counts the number
        of flagged samples out of the total; the percentage is of the
        whole sweep.  <em>suspected clipping (legacy heuristic)</em>
        appears for datasets loaded without a compliance column.</td>
  </tr>
  <tr>
    <td class="k">Model Comparison</td>
    <td>The other registered models evaluated on the same data.
        &blacktriangleright; marks the active model.  R&sup2; / NRMSE
        are <em>not</em> comparable across different data bases
        (raw vs corrected); use the comparison to sanity-check T<sub>e</sub>
        consistency across models, not to pick a winner by R&sup2; alone.</td>
  </tr>
</table>

<h2>Ion composition and n<sub>i</sub> bias (O<sub>2</sub>,
  N<sub>2</sub>, H<sub>2</sub> plasmas)</h2>
<p>
  The Bohm density formula needs the <em>positive-ion</em> mass,
  not the neutral-gas mass.  For monatomic feed gases (Ar, He,
  Ne, Xe, Kr) these are the same thing and nothing changes.
  Molecular feed gases (O<sub>2</sub>, N<sub>2</sub>, H<sub>2</sub>)
  dissociate in the discharge, and the dominant positive ion can
  be the <span class="term">molecule</span> (e.g.
  O<sub>2</sub><sup>+</sup>) or the <span class="term">atom</span>
  (e.g. O<sup>+</sup>) depending on power, pressure and
  geometry.  Because
  <span class="term">n<sub>i</sub> &prop; 1 / &radic;m<sub>i</sub></span>,
  assuming the wrong one biases the inferred density by up to
  ~40&thinsp;% for O<sub>2</sub> (mass ratio 32:16 &rarr; density
  ratio &radic;2).
</p>
<p>
  The <em>Experiment&hellip;</em> dialog exposes this as an
  <em>Ion composition</em> combo with three operator-facing modes:
</p>
<table class="opts">
  <tr>
    <td class="k">Molecular ion</td>
    <td>The dominant positive ion is the molecule (default; typical
        for low-to-moderate power magnetron discharges in
        O<sub>2</sub>).</td>
  </tr>
  <tr>
    <td class="k">Atomic ion</td>
    <td>The dominant positive ion is the dissociated atom (typical
        for high-density / low-pressure ICP or ECR sources where
        the O<sub>2</sub> is mostly dissociated).</td>
  </tr>
  <tr>
    <td class="k">Mixed</td>
    <td>You have a best estimate for the atomic-ion fraction
        <span class="term">x</span> and its half-width
        uncertainty <span class="term">&Delta;x</span>.  The
        effective ion mass is a linear interpolation
        <span class="term">m = (1&minus;x)&middot;m<sub>mol</sub>
        + x&middot;m<sub>atomic</sub></span>, and the
        n<sub>i</sub> CI is widened via
        <span class="term">&sigma;<sub>m</sub>/m =
        |m<sub>mol</sub>&minus;m<sub>atomic</sub>|&middot;
        &Delta;x / m</span>.  Choose this when you do know
        roughly what fraction of the positive ions is atomic
        (e.g. "about 30 %, ±10 %").</td>
  </tr>
  <tr>
    <td class="k">Unknown</td>
    <td>You do not know the composition.  The software uses the
        mid-point mass AND widens the n<sub>i</sub> CI to span
        the full molecular&harr;atomic bracket.  The
        <em>n<sub>i</sub> CI scope label</em> in the result block
        then includes <code>ion_mix</code> so nobody reads the
        reported width as if the composition were known.  Prefer
        Unknown to Mixed when you would otherwise have to guess
        <span class="term">x</span>; Unknown is the safer default
        in that case.</td>
  </tr>
</table>
<p class="note">
  This is a pragmatic first correction &mdash; the software does
  <em>not</em> run a plasma-chemistry model and does not solve for
  the actual dissociation fraction.  When <em>Unknown</em> is
  selected the CI becomes deliberately wide; that is the honest
  answer if you do not know the dominant positive ion.
</p>

<h3>Presets</h3>
<p>
  The <em>Experiment&hellip;</em> dialog offers a preset combo at
  the top of the Ion-composition group for common plasma regimes
  (inert monatomic, O<sub>2</sub> magnetron molecular-ion-dominant,
  O<sub>2</sub> high-power atomic-ion-rich, N<sub>2</sub> molecular,
  H<sub>2</sub> mixed, unknown).  Picking a preset fills the
  Mode / <span class="term">x</span> / <span class="term">&Delta;x</span>
  fields from a curated lookup; editing any of them manually after
  that reverts the preset combo to <em>Custom</em> so the UI state
  never lies about which preset is in effect.  The chosen preset
  is persisted in the analysis sidecar.
</p>
<p>
  Presets are a convenience, <span class="warn">not</span> a
  plasma-chemistry solver.  They are conservative where the regime
  is genuinely uncertain (e.g. <em>O<sub>2</sub> high-power</em>
  uses <span class="term">x = 70&thinsp;%, &Delta;x = 20&thinsp;%</span>).
  If you do not know the regime at all, choose
  <em>Unknown &mdash; widen CI</em>.  All three probe methods
  (Single, Double, Triple) consume the same preset / composition
  settings: the values you pick in Experiment&hellip; apply across
  the whole application, not just the method whose options dialog
  is open.
</p>

<h2>When to distrust the numbers</h2>
<ul>
  <li><span class="warn">Fit warning</span> banner with a clipping
      reason &rarr; re-acquire with higher compliance or accept that
      the numbers are biased.</li>
  <li><em>Fit-only</em> caveat on n<sub>i</sub>: without a quantified
      probe-area and ion-mass uncertainty, the displayed CI is an
      underestimate of the total uncertainty.</li>
  <li>Heavy asymmetry in the saturation levels: switch to the
      <em>tanh + slope + asymmetry</em> model only if there is a
      physical reason to expect asymmetric tips.</li>
  <li>Strong hysteresis warning: the plasma drifted during the sweep;
      averaging forward and reverse branches can help but does not
      fix a true drift.</li>
</ul>
"""


def HELP_HTML() -> str:
    """Full Double-probe help HTML, assembled at call time so a future
    style edit can be applied without stale caches.
    """
    return help_style_block() + HELP_BODY


# ---------------------------------------------------------------------------
# Qt dialog
# ---------------------------------------------------------------------------
def open_double_help_dialog(parent=None) -> None:
    """Modal helper: build, show, and dispose of the Double-probe
    help window.  Safe to call repeatedly."""
    dlg = DoubleAnalysisHelpDialog(parent=parent)
    dlg.exec()


class DoubleAnalysisHelpDialog:
    """Read-only help window for the Double-probe analysis.

    Renders :func:`HELP_HTML` inside a scrollable
    :class:`QTextBrowser`, sized large enough that the typical
    operator does not need to resize on first open.  Imports Qt
    lazily so this module stays import-cheap for headless tests.
    """

    DEFAULT_SIZE = (820, 680)

    def __init__(self, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox)
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle(
            "Double-probe analysis \u2014 help")
        self._dlg.setMinimumSize(*self.DEFAULT_SIZE)
        self._dlg.resize(*self.DEFAULT_SIZE)

        layout = QVBoxLayout(self._dlg)
        layout.setContentsMargins(10, 10, 10, 10)

        self.txt = QTextBrowser(self._dlg)
        self.txt.setOpenExternalLinks(False)
        # Pin colours so the help document does not inherit a palette
        # that would paint bright text on the dark formula panels.
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
            # Mirror the most contrast-critical rules on the
            # QTextDocument level so they apply even if the inline
            # <style> block is stripped downstream.
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
    "HELP_BODY", "HELP_HTML", "help_style_block",
    "HELP_FONT_FAMILY", "HELP_BODY_FONT_SIZE_PT",
    "HELP_LINE_HEIGHT", "HELP_FORMULA_FONT_SIZE_PT",
    "open_double_help_dialog", "DoubleAnalysisHelpDialog",
]
