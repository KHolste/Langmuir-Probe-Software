"""End-user documentation dialog for the Single-probe analysis.

A standalone Qt dialog that explains every Single-probe option and
the analysis pipeline in lab-friendly language.  Open it via the
"Help" button on :class:`dlp_single_options.SingleAnalysisOptionsDialog`
or directly via :func:`open_single_help_dialog`.

The dialog is intentionally self-contained: it imports Qt only when
instantiated (so importing this module in a headless context — for
tests, packaging — is cheap) and renders the documentation as Qt
HTML rich text.  No external network calls.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# HTML body — exposed as a module-level constant so headless tests can
# assert content without instantiating Qt.  Uses Qt-supported HTML
# (QTextDocument subset): block elements, basic CSS, &nbsp; spacing,
# entity references for greek letters and math operators.  Formulas
# are rendered with a monospace font and explicit superscript /
# subscript HTML tags so they read like real equations.
# ---------------------------------------------------------------------------
HELP_HTML = """
<style>
  /* Dark-theme native palette.  Every colour is set explicitly so
     the document does not inherit OS / palette defaults that would
     produce white-on-white formulas (the previous shipping bug).
     All foreground colours pass WCAG AA contrast against the
     #1e2126 base background defined in the QTextBrowser stylesheet.
     Font size / family match dlp_double_help so both help dialogs
     are visually consistent and more readable as documentation. */
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
    color: #7fc8ff;            /* warm pale blue — pops on dark */
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
  /* Formula block: explicit dark surface + bright body so it stays
     readable regardless of inherited text colour.  Accent border
     keeps the visual hierarchy of the previous design. */
  .formula {
    font-family: Consolas, 'Cascadia Code', 'Courier New', monospace;
    font-size: 13pt;
    background: #262a30;
    color: #ffe9a8;            /* warm pale yellow for math */
    border-left: 3px solid #7fc8ff;
    padding: 7px 11px;
    margin: 7px 0;
    display: block;
  }
  /* Inline variable / term name in body prose. */
  .term {
    color: #9adcff;            /* slightly lighter than h2 to read
                                   as inline emphasis */
    font-weight: bold;
  }
  /* Operator-warning / caveat note. */
  .note { color: #f0c060; }
  /* Definition tables (option list, result-block legend). */
  table.opts { border-collapse: collapse; margin: 6px 0; }
  table.opts td {
    vertical-align: top;
    padding: 4px 10px;
    color: #e6e6e6;
  }
  table.opts td.k {
    color: #9adcff;            /* match .term so option keys read as
                                   variable names */
    font-weight: bold;
    white-space: nowrap;
  }
  i { color: #cfd6dd; }        /* italic emphasis stays readable */
  a { color: #5bcaff; }
</style>

<h2>Single-probe analysis &mdash; what it does</h2>
<p>The Single-probe pipeline analyses a single Langmuir probe sweep
&mdash; one electrode, voltage swept while the current to ground is
recorded &mdash; and reports five quantities:
<span class="term">V<sub>f</sub></span> (floating potential),
<span class="term">T<sub>e</sub></span> (electron temperature),
<span class="term">I<sub>i,sat</sub></span> (ion saturation current),
<span class="term">V<sub>p</sub></span> (plasma potential), and
<span class="term">n<sub>e</sub></span> (electron density).
The pipeline is staged: each step records a status, and a failure
in any one step does not abort the run &mdash; downstream
quantities are simply marked <i>not available</i>.</p>

<h3>Pipeline stages</h3>
<table class="opts">
  <tr><td class="k">1. V<sub>f</sub></td>
      <td>Linear interpolation of the I&ndash;V zero crossing.
          The potential where the probe collects equal ion and
          electron flux. No gas constants needed.</td></tr>
  <tr><td class="k">2. T<sub>e</sub> seed</td>
      <td>Coarse semilog slope above V<sub>f</sub>. Used only as a
          first guess to size the proper T<sub>e</sub> fit window
          in step&nbsp;4. <i>This is the &ldquo;Te seed&rdquo;
          referenced in the options.</i></td></tr>
  <tr><td class="k">3. I<sub>i,sat</sub></td>
      <td>Linear fit on the deeply-negative branch
          (V&nbsp;&lt;&nbsp;V<sub>f</sub>&minus;3T<sub>e</sub>),
          where the probe is in ion saturation.</td></tr>
  <tr><td class="k">4. T<sub>e</sub> (refined)</td>
      <td>Semilog fit on the retarding region above V<sub>f</sub>,
          using <span class="formula">I<sub>e</sub>(V) =
          I<sub>tot</sub>(V) + I<sub>i,sat</sub></span>.
          Width of the window is controlled by the
          <span class="term">window&nbsp;width</span> option below.</td></tr>
  <tr><td class="k">5. V<sub>p</sub></td>
      <td>Plasma potential. Two estimators are computed and
          cross-checked &mdash; see <i>V<sub>p</sub> method</i>
          below.</td></tr>
  <tr><td class="k">6. I<sub>e,sat</sub></td>
      <td>Linear fit on V&nbsp;&gt;&nbsp;V<sub>p</sub>+2T<sub>e</sub>
          (the electron-saturation tail), evaluated at V<sub>p</sub>
          for the reported value.</td></tr>
  <tr><td class="k">7. n<sub>e</sub></td>
      <td>Bohm flux from I<sub>i,sat</sub>:
          <span class="formula">n<sub>e</sub> =
          I<sub>i,sat</sub>&nbsp;/&nbsp;(0.6 e A v<sub>Bohm</sub>)
          &nbsp;&nbsp;with&nbsp;&nbsp;
          v<sub>Bohm</sub> = &radic;(k T<sub>e</sub>/m<sub>i</sub>)</span>
          Gas mass m<sub>i</sub> is taken from the configured gas
          mix; defaults to Argon when nothing is configured (a
          warning is logged in that case).</td></tr>
</table>

<h2>The options &mdash; what each one does</h2>

<h3>T<sub>e</sub> fit &mdash; window width</h3>
<p>The retarding region used for the semilog T<sub>e</sub> fit has
the form <span class="formula">V<sub>f</sub> &lt; V &le;
V<sub>f</sub> + f &middot; T<sub>e,seed</sub></span>
where <span class="term">f</span> is this option's value.</p>
<table class="opts">
  <tr><td class="k">2.0&nbsp;&times;</td>
      <td><b>Tighter window.</b> Less influence from the sheath
          onset near V<sub>p</sub>. Use on low-noise sweeps.</td></tr>
  <tr><td class="k">3.0&nbsp;&times;</td>
      <td><b>Default.</b> Matches the historic shipping behaviour;
          good compromise between bias and noise.</td></tr>
  <tr><td class="k">5.0&nbsp;&times;</td>
      <td><b>Wider window.</b> More signal-to-noise on noisy
          sweeps at the cost of stronger sheath influence
          (potentially biasing T<sub>e</sub> downward).</td></tr>
</table>

<h3>T<sub>e</sub> fit &mdash; method</h3>
<p><span class="term">Huber-loss</span> (default): a robust linear
regression that down-weights single outlying points (a clipped
sample, a noise glitch) instead of letting them tilt the slope. If
SciPy is unavailable, falls back transparently to ordinary least
squares (OLS). Disable only for back-to-back comparison with the
legacy OLS path.</p>

<h3>Data handling &mdash; compliance</h3>
<p>The SMU emits a per-point compliance flag whenever the requested
voltage drove the current to its limit.  Such points are clipped:
they carry no useful physics.</p>
<table class="opts">
  <tr><td class="k">Exclude clipped (default)</td>
      <td>Drop compliance-flagged points before any fit.  The
          compact HTML reports a small &ldquo;Compliance:
          N&nbsp;points excluded&rdquo; provenance row.</td></tr>
  <tr><td class="k">Include all (legacy)</td>
      <td>Use every acquired sample.  Restores pre-hardening
          behaviour for direct comparison; <i>not recommended</i>
          for production sweeps with known compliance hits.</td></tr>
</table>

<h3>Data handling &mdash; hysteresis warning threshold</h3>
<p>Bidirectional sweeps (forward + reverse) should retrace.  This
option sets the divergence threshold above which a non-blocking
&ldquo;plasma drift&rdquo; warning is logged.  The metric is
<span class="formula">max&nbsp;|I<sub>fwd</sub>&minus;I<sub>rev</sub>|
&nbsp;/&nbsp;|I|<sub>max</sub></span> in percent.  5&nbsp;% is
typical lab tolerance.</p>

<h3>Data handling &mdash; V<sub>p</sub> method</h3>
<p>Two independent estimators are always computed and reported
side-by-side.  This option chooses which one fills the headline
&ldquo;V<sub>p</sub>&rdquo; field.</p>
<table class="opts">
  <tr><td class="k">Auto (default)</td>
      <td>Use the derivative method when its quality scores
          <i>high</i> confidence; otherwise fall back to the
          intersection method.  Recommended for normal use.</td></tr>
  <tr><td class="k">Derivative</td>
      <td>Smoothed dI/dV peak detection
          (Savitzky&ndash;Golay filter).  Theoretically the
          inflection point of I(V); accurate on clean sweeps.
          Falls back to intersection when the derivative cannot
          be evaluated.</td></tr>
  <tr><td class="k">Intersection (legacy)</td>
      <td>Cross-point of the log-linear retarding line and the
          linear electron-saturation line.  Robust to noise but
          biased on soft knees; was the only method in earlier
          versions.</td></tr>
</table>
<p class="note">When the two estimators disagree by more than
T<sub>e</sub>, a warning is emitted and the V<sub>p</sub>-check
row in the result HTML is highlighted in amber &mdash; this
usually signals a soft knee or an oblique electron-saturation
slope.</p>

<h3>Confidence interval &mdash; bootstrap T<sub>e</sub> CI</h3>
<p>Optional non-parametric bootstrap of the T<sub>e</sub> semilog
fit.  When enabled, the same fit window is re-sampled with
replacement N&nbsp;times (default 200) and the 2.5&nbsp;% /
97.5&nbsp;% percentiles of the resulting T<sub>e</sub>
distribution are reported as a 95&nbsp;% confidence interval.
Adds &lt;&nbsp;1&nbsp;ms per sweep at default N.</p>
<p>If the bootstrap cannot run (too few valid resamples, or
T<sub>e</sub> itself not determined), the CI row reads
&ldquo;n/a (insufficient data for bootstrap)&rdquo; rather than
silently dropping &mdash; honest about the limit instead of
implying false precision.</p>

<h2>How to read the result block</h2>
<table class="opts">
  <tr><td class="k">V<sub>f</sub></td><td>floating potential, V</td></tr>
  <tr><td class="k">V<sub>p</sub></td>
      <td>plasma potential, V &mdash; with the chosen
          <i>method</i> and <i>confidence</i> tag in parentheses.
          A solid plot overlay marks <i>high</i> confidence,
          dashed = <i>medium</i>, dotted = <i>low</i>.</td></tr>
  <tr><td class="k">V<sub>p</sub> check</td>
      <td>both V<sub>p</sub> candidates side-by-side
          plus their disagreement &Delta;V; only shown when both
          methods produced a value.</td></tr>
  <tr><td class="k">T<sub>e</sub></td>
      <td>electron temperature in eV
          (= k<sub>B</sub>T<sub>e</sub>/e), &plusmn; standard
          error from the slope variance.</td></tr>
  <tr><td class="k">T<sub>e</sub> CI</td>
      <td>95&nbsp;% bootstrap CI, only shown when bootstrap is
          enabled.</td></tr>
  <tr><td class="k">I<sub>i,sat</sub>, I<sub>e,sat</sub></td>
      <td>ion / electron saturation currents (&micro;A / mA).</td></tr>
  <tr><td class="k">n<sub>e</sub></td>
      <td>electron density from Bohm flux (m<sup>&minus;3</sup>).</td></tr>
  <tr><td class="k">T<sub>e</sub> fit</td>
      <td>quality of the semilog fit:
          R<sup>2</sup>, NRMSE, n samples used.</td></tr>
</table>

<h2>References</h2>
<p>Hutchinson, <i>Principles of Plasma Diagnostics</i>, 2nd ed.,
Cambridge University Press 2002, ch.&nbsp;3 &mdash; Langmuir
probe theory.<br>
Chen, <i>Lecture Notes on Langmuir Probe Diagnostics</i>, 2003
&mdash; practical fitting recipes.<br>
Mausbach, J. Vac. Sci. Technol. A 15, 2923 (1997) &mdash; on
derivative-based V<sub>p</sub> determination.</p>
"""


def open_single_help_dialog(parent=None) -> None:
    """Modal helper: build, show, and clean up the Single-probe
    help window.  Safe to call repeatedly."""
    dlg = SingleAnalysisHelpDialog(parent=parent)
    dlg.exec()


class SingleAnalysisHelpDialog:
    """Read-only help window for the Single-probe analysis.

    Renders :data:`HELP_HTML` inside a scrollable
    :class:`QTextBrowser`, sized large enough that the typical
    operator does not need to resize on first open.  Imports Qt
    lazily so this module stays import-cheap for headless tests.
    """

    DEFAULT_SIZE = (760, 640)

    def __init__(self, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox)
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle(
            "Single-probe analysis &mdash; help")
        self._dlg.setMinimumSize(*self.DEFAULT_SIZE)
        # Preferred size — the dialog opens here unless the user
        # has set a different geometry via Qt window-state restore.
        self._dlg.resize(*self.DEFAULT_SIZE)

        layout = QVBoxLayout(self._dlg)
        layout.setContentsMargins(10, 10, 10, 10)

        self.txt = QTextBrowser(self._dlg)
        self.txt.setOpenExternalLinks(False)
        # Pin the QTextBrowser's own colours so the document does
        # NOT inherit whatever the OS / parent palette provides.
        # Without this, the embedded CSS could end up rendering
        # bright (palette-default) body text on top of the formula
        # block's dark panel — readable — but on some Windows dark-
        # mode configurations the inherited body fg is near-white
        # AND the previous shipping CSS used a near-white panel,
        # producing the white-on-white bug we are fixing here.  An
        # explicit stylesheet keeps the rendering deterministic.
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
            # Mirrors the most contrast-critical rules on the
            # QTextDocument level so they apply even if the inline
            # <style> block is ever stripped (e.g. when the help
            # text is reused outside the QTextBrowser, or when an
            # operator copies it to another viewer).
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

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self._dlg.reject)
        btns.accepted.connect(self._dlg.accept)
        # Close-button-only: connect *both* roles to accept/reject.
        for btn in btns.buttons():
            btn.clicked.connect(self._dlg.accept)
        layout.addWidget(btns)

    def exec(self):
        return self._dlg.exec()
