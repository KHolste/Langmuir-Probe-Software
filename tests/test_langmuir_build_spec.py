"""Static contract tests for the LangmuirMeasure PyInstaller spec.

These tests do not run PyInstaller.  They parse
``LangmuirMeasure.spec`` as text + AST and assert invariants that
protect the freeze:

1. Every entry in the spec's ``REQUIRED_LOCAL`` list is also present in
   the standalone ``tools/check_langmuir_build_env.py`` mirror, so the
   two stay in sync.
2. The local-module imports actually issued by the entry-point chain
   (``LPmeasurement``, ``DoubleLangmuir_measure_v2``,
   ``DoubleLangmuir_measure``, ``dlp_lp_window``) are all covered by
   the spec's ``REQUIRED_LOCAL`` list - a future "from new_module
   import ..." without a spec entry will fail this test instead of
   silently producing a broken installer.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SPEC = REPO / "LangmuirMeasure.spec"
TOOL = REPO / "tools" / "check_langmuir_build_env.py"

# Top-level files whose import graph the LP freeze must cover.  The
# discovery walk picks up local-module "from foo import bar" statements
# via AST; the test then asserts they are all declared in
# REQUIRED_LOCAL.
ENTRY_POINTS = (
    REPO / "LPmeasurement.py",
    REPO / "DoubleLangmuir_measure_v2.py",
    REPO / "DoubleLangmuir_measure.py",
    REPO / "dlp_lp_window.py",
)


def _parse_required_local(path: pathlib.Path) -> list[str]:
    """Return the REQUIRED_LOCAL list literal from a spec/tool file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "REQUIRED_LOCAL"
                and isinstance(node.value, ast.List)):
            return [elt.value for elt in node.value.elts
                    if isinstance(elt, ast.Constant)
                    and isinstance(elt.value, str)]
    raise AssertionError(f"REQUIRED_LOCAL not found in {path}")


def _local_module_names() -> set[str]:
    """All importable local module names at the project root."""
    return {p.stem for p in REPO.glob("*.py")
            if p.is_file() and not p.stem.startswith("_")}


def _imports_in(path: pathlib.Path) -> set[str]:
    """Top-level local-module names imported by ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".", 1)[0])
    return found


# ---------------------------------------------------------------------------
class TestSpecContract:
    def test_entry_point_present_in_spec(self):
        items = _parse_required_local(SPEC)
        assert "LPmeasurement" in items
        assert "DoubleLangmuir_measure_v2" in items
        assert "DoubleLangmuir_measure" in items

    def test_iter4d_persistence_modules_present(self):
        """Regression: analysis-history, analysis-log, and VISA
        persistence must stay in the freeze - without them the Analyse
        log and the VISA scan cache silently break in a frozen bundle.
        """
        items = _parse_required_local(SPEC)
        for required in ("analysis_history", "analysis_log_window",
                         "visa_persistence"):
            assert required in items, required

    def test_spec_and_tool_lists_match(self):
        spec_items = _parse_required_local(SPEC)
        tool_items = _parse_required_local(TOOL)
        assert set(spec_items) == set(tool_items), (
            f"spec vs tool drift - only-in-spec: "
            f"{set(spec_items) - set(tool_items)}, only-in-tool: "
            f"{set(tool_items) - set(spec_items)}")

    def test_no_duplicates_in_required_local(self):
        items = _parse_required_local(SPEC)
        assert len(items) == len(set(items)), \
            "REQUIRED_LOCAL contains duplicates"


class TestImportGraphCoverage:
    def test_entry_point_imports_are_all_covered(self):
        """Every local-module top-level import in the LP entry-point
        chain must be declared in REQUIRED_LOCAL.  A new "from
        new_module import ..." without a spec update will fail this
        test instead of shipping a freeze that crashes on startup."""
        spec_items = set(_parse_required_local(SPEC))
        local_names = _local_module_names()
        observed: set[str] = set()
        for ep in ENTRY_POINTS:
            observed |= _imports_in(ep)
        local_imports = observed & local_names
        missing = local_imports - spec_items
        assert not missing, (
            f"Entry-point imports not declared in REQUIRED_LOCAL: "
            f"{sorted(missing)}")

    def test_spec_required_local_only_lists_real_modules(self):
        """A typo in REQUIRED_LOCAL would point at a non-existent file
        and the spec's import-time sanity loop would abort the build.
        This test surfaces the typo at unit-test time instead."""
        spec_items = _parse_required_local(SPEC)
        local_names = _local_module_names()
        unknown = [name for name in spec_items if name not in local_names]
        assert not unknown, (
            f"REQUIRED_LOCAL references non-existent project files: "
            f"{unknown}")
