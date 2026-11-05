import math
from pathlib import Path

import pytest

from mpas_profile.timing import parse_log

SAMPLE = Path(__file__).parent / "data" / "log.atmosphere.sample.out"
# The sample is a trimmed copy of a real 10 km, 1536-rank run: its header, mesh
# dimensions, the first four steps, and the end of the log (timer table included).


@pytest.fixture(scope="module")
def run():
    return parse_log(SAMPLE)


def test_header(run):
    assert run.version == "8.4.2-noaa"
    assert run.describe == "MPAS-v8.4.2-1.19"
    assert (run.build_target, run.precision, run.io_layer) == ("IntelLLVM", "single", "SMIOL")
    assert run.openmp is False
    assert run.ntasks == 1536
    assert (run.ncells, run.nlevels) == (5898242, 124)


def test_open_and_close_times(run):
    assert run.opened.isoformat() == "2026-11-04T16:55:46"
    assert run.closed.isoformat() == "2026-11-04T18:50:10"
    assert run.log_wall == 6864.0


def test_steps(run):
    assert run.step_times == [16.5524, 3.44538, 3.32946, 3.30426]
    assert run.nsteps == len(run.step_stamps) == len(run.max_wsp) == 4
    assert run.dt == 50.0 and run.sim_seconds == 200.0
    assert run.step_stamps[0].isoformat() == "2026-01-26T00:00:00"


def test_io_lines(run):
    assert run.stream_output == [39.5108, 96.5201]
    assert run.diagnostics == [3.87619, 4.64958]
    assert run.reads == {"input": 40.0924}
    assert run.stream_input == [pytest.approx(5.61898e-05)]   # Fortran E-format


def test_footer_and_status(run):
    assert run.messages == {"output": 42190, "warnings": 10, "errors": 0, "criticals": 0}
    assert run.finished and run.logging_complete and run.completed


def test_timer_table_rows(run):
    assert len(run.timers) == 32
    total = run.timer("total time")
    assert (total.level, total.path, total.total, total.calls) == (1, "total time", 6863.79492, 1)
    assert total.par_eff == 1.0 and total.pct_tot == 100.0


def test_timer_names_with_spaces_and_paths(run):
    ti = run.timer("time integration")
    assert ti.path == "time integration" and ti.level == 2
    assert (ti.total, ti.calls, ti.min, ti.max) == (6294.771, 1728, 3.27815, 16.55586)
    assert (ti.pct_tot, ti.pct_par, ti.par_eff) == (91.71, 91.71, 1.0)
    sw = run.timer("time integration/physics driver/rrtmg_swrad")
    assert sw.level == 4 and sw.name == "rrtmg_swrad"
    assert (sw.total, sw.calls, sw.par_eff) == (316.10410, 48, 0.47)
    # a leaf name finds the same row as the full path
    assert run.timer("rrtmg_swrad") is sw
    assert run.timer("no such timer") is None


def test_children_follow_the_tree(run):
    assert [t.name for t in run.children("total time")] == [
        "initialize", "diagnostic_fields", "stream_output", "stream_input", "time integration"]
    assert [t.name for t in run.children("initialize")] == [
        "mpas_block_decomp_cells_for_proc", "read_ICs"]
    assert [t.name for t in run.children("physics driver")][:2] == [
        "calc_cldfraction", "rrtmg_swrad"]
    assert run.children("rrtmg_swrad") == []          # a leaf
    assert run.children("no such timer") == []
    # level-3 siblings of physics driver are not its children
    assert "atm_compute_dyn_tend" not in [t.name for t in run.children("physics driver")]


def test_truncated_before_the_table(tmp_path):
    lines = SAMPLE.read_text().splitlines(keepends=True)
    cut = next(i for i, l in enumerate(lines) if "Timer information" in l)
    p = tmp_path / "log.atmosphere.0000.out"
    p.write_text("".join(lines[:cut]))
    r = parse_log(p)
    assert r.nsteps == 4 and r.timers == []
    assert not r.completed and r.messages == {}


def test_truncated_in_the_table(tmp_path):
    lines = SAMPLE.read_text().splitlines(keepends=True)
    cut = next(i for i, l in enumerate(lines) if "time integration" in l and "6294" in l)
    p = tmp_path / "log.atmosphere.0000.out"
    p.write_text("".join(lines[:cut]))
    r = parse_log(p)
    assert 0 < len(r.timers) < 32 and not r.completed
    assert r.timers[-1].name != "time integration"


def test_overflowed_field_is_nan(tmp_path):
    # f15.5 prints stars when a value does not fit; the field keeps its 15 columns.
    text = SAMPLE.read_text().replace("     6863.79492", "*" * 15, 1)
    p = tmp_path / "log.atmosphere.0000.out"
    p.write_text(text)
    r = parse_log(p)
    assert math.isnan(r.timer("total time").total)
    assert r.timer("total time").calls == 1 and r.timer("time integration").total == 6294.771


def test_empty_and_binary_garbage(tmp_path):
    p = tmp_path / "log.atmosphere.0000.out"
    p.write_bytes(b"")
    r = parse_log(p)
    assert (r.nsteps, r.timers, r.ntasks, r.dt, r.sim_seconds) == (0, [], None, None, None)
    p.write_bytes(b"\xff\xfe junk \x00\x01\n Timing for integration step: 3.5 s\n")
    assert parse_log(p).step_times == [3.5]
