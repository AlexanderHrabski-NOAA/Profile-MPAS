from pathlib import Path

import pytest

import logsynth
from mpas_profile import SetupError, analysis

SAMPLE = Path(__file__).parent / "data" / "log.atmosphere.sample.out"


@pytest.fixture
def study(tmp_path):
    return logsynth.write_study(tmp_path / "s")


def test_duration_seconds():
    assert analysis.duration_seconds("1_00:00:00") == 86400
    assert analysis.duration_seconds("7_00:00:00") == 7 * 86400
    assert analysis.duration_seconds("06:30:15") == 6 * 3600 + 30 * 60 + 15
    assert analysis.duration_seconds("soon") is None


# ---- discovery ---------------------------------------------------------------------------

def test_find_logs_in_a_study_tree(study):
    logs = analysis.find_logs([study])
    assert [p.parent.name for p in logs] == [
        "60km_native_n1x96", "60km_native_n2x96", "60km_native_n4x96", "60km_native_n8x96"]


def test_find_logs_accepts_a_file_a_dir_or_both_without_duplicates(study):
    one = study / "experiments" / "60km_native_n2x96"
    assert len(analysis.find_logs([one])) == 1
    assert len(analysis.find_logs([one / "log.atmosphere.0000.out"])) == 1
    assert len(analysis.find_logs([study, one])) == 4


def test_find_logs_skips_builds(study):
    (study / "builds" / "abc-native").mkdir(parents=True)
    (study / "builds" / "abc-native" / "log.atmosphere.0000.out").write_text("")
    assert len(analysis.find_logs([study])) == 4


def test_find_logs_errors(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SetupError, match="No log.atmosphere.0000.out found"):
        analysis.find_logs([tmp_path / "empty"])
    with pytest.raises(SetupError, match="no such file"):
        analysis.find_logs([tmp_path / "missing"])
    with pytest.raises(SetupError, match="No logs given"):
        analysis.find_logs([])


def test_load_runs_reads_provenance_and_orders_by_size(study):
    runs = analysis.load_runs([study])
    assert [r.ntasks for r in runs] == [96, 192, 384, 768]
    r = runs[2]
    assert (r.label, r.nodes, r.resolution, r.mode, r.output, r.pio) == (
        "60km_native_n4x96", 4, "60km", "native", True, "default")


def test_old_boolean_profile_in_provenance(tmp_path):
    logsynth.write_experiment(tmp_path, "a", 1, profile=True)
    logsynth.write_experiment(tmp_path, "b", 2, profile=False)
    assert {r.label: r.mode for r in analysis.load_runs([tmp_path])} == {
        "a": "scalasca", "b": "native"}


def test_bare_log_without_provenance(tmp_path):
    d = logsynth.write_experiment(tmp_path, "bare", 2, provenance=False)
    (run,) = analysis.load_runs([d / "log.atmosphere.0000.out"])
    assert run.label == "bare" and run.nodes is None and run.mode is None
    assert run.ntasks == 192 and run.resolution == "40962 cells"
    s = run.summary
    assert s["nodes"] is None and s["node_hours_per_sim_day"] is None
    assert s["steps_expected"] is None and s["sim_days_per_day"] > 0


def test_corrupt_provenance_is_ignored(tmp_path):
    d = logsynth.write_experiment(tmp_path, "x", 1)
    (d / "provenance.yaml").write_text("experiment: [unclosed\n")
    (run,) = analysis.load_runs([d])
    assert run.meta == {} and run.ntasks == 96


def test_duplicate_labels_are_made_distinct(tmp_path):
    for sub in ("one", "two"):
        logsynth.write_experiment(tmp_path / sub, "same", 1, provenance=False)
    assert [r.label for r in analysis.load_runs([tmp_path])] == ["same", "same#2"]


# ---- summaries ---------------------------------------------------------------------------

def test_summary_of_the_real_log_excerpt():
    (run,) = analysis.load_runs([SAMPLE])
    s = run.summary
    assert s["wall_s"] == 6863.79492 and s["integration_s"] == 6294.771
    assert s["initialize_s"] == 54.52267 and s["read_ics_s"] == 40.09858
    assert s["output_s"] == pytest.approx(39.5108 + 96.5201)
    assert s["output_writes"] == 2
    assert (s["ranks"], s["cells"], s["levels"]) == (1536, 5898242, 124)
    assert s["cells_per_rank"] == pytest.approx(5898242 / 1536)
    assert s["status"] == "ok" and s["warnings"] == 10 and s["errors"] == 0
    assert s["version"] == "MPAS-v8.4.2-1.19" and s["dt_s"] == 50.0


def test_throughput_and_cost(tmp_path):
    d = logsynth.write_experiment(tmp_path, "t", 2)
    s = analysis.load_runs([d])[0].summary
    wall = s["wall_s"]
    assert s["sim_s"] == 12 * 50
    assert s["sim_days_per_day"] == pytest.approx(600 / wall)
    assert s["node_hours_per_sim_day"] == pytest.approx(2 * wall / 3600 / (600 / 86400))
    assert s["core_s_per_cell_step"] == pytest.approx(wall * 192 / (40962 * 12))


def test_step_stats_separate_the_first_step_and_spikes():
    # first step is slow (warm-up); every 4th step is a radiation-like spike.
    times = [20.0] + [3.0, 3.0, 3.0, 12.0] * 4
    s = analysis.step_stats(times)
    assert s["step_first_s"] == 20.0 and s["step_median_s"] == 3.0
    assert s["step_mean_s"] == pytest.approx(sum(times) / 17)
    assert s["step_mean_s"] > s["step_median_s"]       # why the mean is the headline
    assert s["spike_steps"] == 4
    assert s["spike_cost_frac"] == pytest.approx(4 * 9.0 / sum(times))
    assert s["step_max_s"] == 20.0
    assert analysis.step_stats([]) == {}
    assert analysis.step_stats([5.0])["step_median_s"] == 5.0


def test_status(tmp_path):
    ok = logsynth.write_experiment(tmp_path, "ok", 1)
    cut = logsynth.write_experiment(tmp_path, "cut", 1, complete=False)
    short = logsynth.write_experiment(tmp_path, "short", 1, nsteps=6)   # expects 12 steps
    bad = logsynth.write_experiment(tmp_path, "bad", 1)
    p = bad / "log.atmosphere.0000.out"
    p.write_text(p.read_text().replace("Error messages =                     0",
                                       "Error messages =                     3"))
    status = {r.label: r.summary["status"] for r in analysis.load_runs([tmp_path])}
    assert status == {"ok": "ok", "cut": "incomplete", "short": "incomplete", "bad": "error"}
    # A log that never closed has no timer table and no end time, so no wall time.
    cut_summary = analysis.load_runs([cut])[0].summary
    assert cut_summary["wall_s"] is None and cut_summary["integration_s"] is None
    # The crash came during step 12: eleven steps completed.
    assert cut_summary["steps"] == 11 and cut_summary["step_mean_s"] > 0


def test_timer_rows_and_components(study):
    run = analysis.load_runs([study])[1]                # 192 ranks
    rows = analysis.timer_rows(run)
    assert {r["run"] for r in rows} == {run.label} and rows[0]["path"] == "total time"
    assert any(r["path"] == "time integration/physics driver/rrtmg_swrad" for r in rows)
    comp = {c[0]: c for c in analysis.components(run, "time integration")}
    name, total, eff, core_s = comp["atm_compute_dyn_tend"]
    assert core_s == pytest.approx(total * 192) and 0 < eff <= 1
    assert "rrtmg_swrad" in {c[0] for c in analysis.components(run, "physics driver")}


# ---- studies -----------------------------------------------------------------------------

def test_scaling_points(study):
    runs = analysis.load_runs([study])
    assert analysis.is_scaling_study(runs)
    pts = analysis.scaling_points(runs, "integration_s")
    assert [p["ranks"] for p in pts] == [96, 192, 384, 768]
    assert pts[0]["speedup"] == 1.0 and pts[0]["efficiency"] == 1.0
    for p in pts[1:]:
        assert p["speedup"] == pytest.approx(p["ideal"] and pts[0]["value"] / p["value"])
        assert p["efficiency"] == pytest.approx(p["speedup"] / (p["ranks"] / 96))
        assert p["efficiency"] < 1 and p["value"] > p["ideal"]
    assert [p["efficiency"] for p in pts] == sorted((p["efficiency"] for p in pts), reverse=True)


def test_scaling_points_skip_runs_without_the_value(tmp_path):
    logsynth.write_experiment(tmp_path, "a", 1)
    logsynth.write_experiment(tmp_path, "b", 2, complete=False)    # no timer table
    runs = analysis.load_runs([tmp_path])
    assert [p["ranks"] for p in analysis.scaling_points(runs, "integration_s")] == [96]
    assert analysis.scaling_points([], "integration_s") == []


def test_single_run_is_not_a_scaling_study(tmp_path):
    logsynth.write_experiment(tmp_path, "a", 1)
    assert not analysis.is_scaling_study(analysis.load_runs([tmp_path]))


def test_groups_separate_series(tmp_path):
    for n in (1, 2):
        logsynth.write_experiment(tmp_path, f"nat{n}", n, profile="native")
        logsynth.write_experiment(tmp_path, f"sca{n}", n, profile="scalasca")
    g = analysis.groups(analysis.load_runs([tmp_path]))
    assert len(g) == 2
    assert {k[1] for k in g} == {"native", "scalasca"}
    assert all([r.ntasks for r in rs] == [96, 192] for rs in g.values())
    assert analysis.group_label(("60km", "native", False, "1/node")) == \
        "60km · native · no output · pio 1/node"


def test_caveats(tmp_path):
    logsynth.write_experiment(tmp_path, "a", 1, profile="native")
    logsynth.write_experiment(tmp_path, "b", 2, profile="scalasca")
    notes = analysis.caveats(analysis.load_runs([tmp_path]))
    assert any("mixed" in n for n in notes)
    only = tmp_path / "only"
    logsynth.write_experiment(only, "c", 1, profile="scalasca")
    assert any("Scalasca-instrumented" in n for n in analysis.caveats(analysis.load_runs([only])))
    logsynth.write_experiment(only, "d", 2, profile="scalasca", complete=False)
    assert any("not finished cleanly: d" in n for n in analysis.caveats(analysis.load_runs([only])))
