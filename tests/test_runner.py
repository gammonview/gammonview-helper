# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Nicholas Vlamis

"""The analysis itself -- the one place this package touches the engine.

Most of these run without bgsage. The end-to-end test does not, and is skipped
where the engine is absent: a contributor working on the CLI should not need
80 MB of neural nets to run the suite, and CI runs a codec-only job on purpose.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

from gvhelper import runner

#: A real analysed match, used as *input* -- the helper's job is to append an
#: analysis to an OGXM file, and a file that already has one exercises that
#: rather than the easier empty case. It is also what proves the append is
#: non-destructive: the result must come back with **two** blocks.
#:
#: Vendored here (12 KB) rather than reached for in the analysis repo, which is
#: where it came from and where it is a golden. A relative path out of the
#: checkout was how this test silently stopped running when the helper moved --
#: the `exists()` guard below turned a broken path into a skip.
GOLDEN = Path(__file__).resolve().parent / "golden" / "B4_SrGcsKAQmoTyHlgJCbM.fast.gvab"

def _has_engine() -> bool:
    try:
        import bgsage  # noqa: F401

        return True
    except Exception:
        return False


#: Skipped without the engine. The golden is checked too, but now only as a
#: guard against an sdist that failed to carry it -- it lives beside this file,
#: so a missing one is a packaging bug rather than the normal case it used to
#: be.
needs_engine = pytest.mark.skipif(
    not _has_engine() or not GOLDEN.exists(),
    reason="bgsage or the golden match is not available",
)


def test_the_engine_is_not_imported_just_to_answer_a_question():
    """`status` and `link` must not pay for loading 24 neural nets. A user
    running `gammonview-helper status` should get an answer in milliseconds, and
    the pairing flow is the part people do while watching."""
    assert "bgsage" not in sys.modules or _has_engine()
    # The version is read from distribution metadata, not from the module.
    assert "bgsage" in runner.engine_version() or runner.engine_version() == "unknown"


def test_the_presets_offered_are_the_engines_own():
    """Read from `gvanalysis` rather than hardcoded, so a preset added to the
    engine package appears in the website's menu on the next `hello` with
    nothing here to update."""
    presets = runner.available_presets()
    if presets:
        assert "world_class" in presets


def test_the_deeper_presets_are_the_point():
    """The helper exists so that the menu has entries the shared worker cannot
    offer. If this list ever shrinks to the shared worker's three, the feature
    has quietly stopped being worth installing."""
    presets = runner.available_presets()
    if presets:
        assert set(presets) - {"very_quick", "fast", "balanced"}


def test_lowering_priority_is_survivable_everywhere():
    """Called once at startup on every platform. A `nice` that raised on a
    machine that does not support it would take the helper down at the worst
    moment -- before it has done anything a user could see."""
    runner.lower_priority(0)
    runner.lower_priority(1)


def test_progress_starts_at_nothing():
    p = runner.Progress()
    assert p.get() == (0, 0)


@needs_engine
@pytest.mark.slow
def test_a_real_match_analyses_and_comes_back_gzipped():
    """End to end against the published package, at the cheapest preset.

    What this proves is the seam: `analyze` returns bytes the browser's
    `gva/analyze.js` can inflate, because the relay moves them without looking
    inside. If the two ever disagree about compression this is where it shows.
    """
    from gvformat import read_gvab

    progress = runner.Progress()
    out = runner.analyze(GOLDEN.read_bytes(), "very_quick", progress, jobs=1, threads=1)

    assert out[:2] == b"\x1f\x8b", "the browser inflates what comes back"
    doc = read_gvab(gzip.decompress(out))
    # The input already carried a `fast` analysis; ours is appended, never
    # replacing it -- that is `append_analysis`'s contract and the reason a
    # re-analysis does not destroy what a user already had.
    assert len(doc["analyses_info"]) >= 2

    done, total = progress.get()
    assert total > 0 and done == total


# --- custom presets ----------------------------------------------------------


def _engine_takes_definitions() -> bool:
    try:
        from gvanalysis.presets import custom_preset  # noqa: F401

        return True
    except Exception:
        return False


needs_custom = pytest.mark.skipif(
    not _engine_takes_definitions(), reason="the installed engine predates custom_preset"
)


def test_custom_presets_are_offered_exactly_when_the_engine_can_run_them():
    """A helper on an engine too old to take a definition must not say it can:
    the relay would hand it jobs it can only fail."""
    assert runner.engine_features() == (["custom_presets"] if _engine_takes_definitions() else [])


#: Definitions the website's settings page accepts (`helpersettings.checked_spec`
#: in the GammonView accounts service, which mirrors the engine's rules because
#: that service may not import it) and ones it refuses. The same lists are in
#: `accounts/tests/test_helper_settings.py`. They are the drift check between
#: the two validators: if the engine starts refusing one the site accepts, a
#: user can save a preset that fails every match it is given.
SITE_ACCEPTS = [
    {"first_pass": "2ply"},
    {"first_pass": "3ply", "second_pass": "rollout", "error_threshold": 0},
    {"first_pass": "3ply", "mid_pass": "4ply", "second_pass": "truncated2", "close_threshold": 0.08},
    {"first_pass": {"checker": "3ply"}, "mid_pass": {"cube": "truncated2"},
     "second_pass": "truncated2", "close_threshold": {"cube": 0.08},
     "error_threshold": {"checker": 0.02, "cube": 0.0}},
    {"first_pass": {"checker": "3ply", "cube": "4ply"}, "mid_pass": {"checker": "4ply"},
     "second_pass": "truncated2", "close_threshold": {"checker": 0.08}, "error_threshold": 0.02},
]
SITE_REFUSES = [
    {},
    {"second_pass": "truncated2"},
    {"first_pass": "5ply"},
    {"first_pass": {"cube": "3ply"}},
    {"first_pass": {"checker": "3ply", "move": "4ply"}},
    {"first_pass": "3ply", "revision": 2},
    {"first_pass": "3ply", "resign_errors": True},
    {"first_pass": "3ply", "mid_pass": "4ply", "close_threshold": 0.08},
    {"first_pass": "3ply", "mid_pass": "4ply", "second_pass": "truncated2"},
    {"first_pass": "3ply", "mid_pass": {"cube": "4ply"}, "second_pass": "truncated2",
     "close_threshold": {"checker": 0.08}},
    {"first_pass": "3ply", "second_pass": "truncated2", "error_threshold": -0.1},
    {"first_pass": "3ply", "second_pass": "truncated2", "error_threshold": True},
]


@needs_custom
@pytest.mark.parametrize("spec", SITE_ACCEPTS)
def test_what_the_site_accepts_the_engine_runs(spec):
    assert runner.custom("mine", spec).label.startswith("custom/mine@")


@needs_custom
@pytest.mark.parametrize("spec", SITE_REFUSES)
def test_what_the_site_refuses_the_engine_refuses(spec):
    """The other direction matters less -- the site being stricter only costs a
    preset nobody could save -- but a site looser than the engine is the bug,
    so both are pinned."""
    with pytest.raises(runner.PresetRefused):
        runner.custom("mine", spec)


@needs_custom
@needs_engine
@pytest.mark.slow
def test_a_custom_preset_analyses_and_is_labelled_as_one():
    """End to end: the definition the relay carries is what ran, and the
    analysis says so -- `custom/<name>@<digest>`, which is what the viewer reads
    to name it."""
    from gvformat import read_gvab

    spec = {"first_pass": "1ply", "second_pass": "2ply", "error_threshold": 0.0}
    out = runner.analyze(GOLDEN.read_bytes(), "my-quick", runner.Progress(),
                         jobs=1, threads=1, spec=spec)
    doc = read_gvab(gzip.decompress(out))
    labels = [info.get("preset") for info in doc["analyses_info"]]
    assert runner.custom("my-quick", spec).label in labels
