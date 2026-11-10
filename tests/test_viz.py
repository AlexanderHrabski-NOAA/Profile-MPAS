import csv
import sys
from pathlib import Path

import pytest

import logsynth
from mpas_profile import analysis, viz

pytest.importorskip("matplotlib")

SAMPLE = Path(__file__).parent / "data" / "log.atmosphere.sample.out"


@pytest.fixture(autouse=True)
def headless(monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "Agg")
    yield
    plt = sys.modules.get("matplotlib.pyplot")       # absent when a test hides matplotlib
    if plt is not None:
        plt.close("all")


@pytest.fixture
def study(tmp_path):
    return logsynth.write_study(tmp_path / "s", node_counts=(1, 2, 4))


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def png_ok(path):
    return path.exists() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_single_run_save(tmp_path, capsys):
    out = tmp_path / "out"
    viz.main([str(SAMPLE), "--save", str(out)])
    assert png_ok(out / "overview.png") and png_ok(out / "components.png")
    assert not (out / "scaling.png").exists()
    (row,) = read_csv(out / "results.csv")
    assert float(row["wall_s"]) == 6863.79492 and row["status"] == "ok"
    assert row["nodes"] == ""                            # unknown without provenance
    timers = read_csv(out / "timers.csv")
    assert len(timers) == 32 and timers[0]["path"] == "total time"
    shown = capsys.readouterr().out
    assert "1 run(s)" in shown and "wrote" in shown


def test_study_save_has_scaling_breakdown_quality(study, tmp_path):
    out = tmp_path / "out"
    viz.main([str(study), "--save", str(out)])
    for name in ("scaling", "breakdown", "quality"):
        assert png_ok(out / f"{name}.png"), name
    rows = read_csv(out / "results.csv")
    assert [int(r["ranks"]) for r in rows] == [96, 192, 384]
    assert all(r["status"] == "ok" and float(r["sim_days_per_day"]) > 0 for r in rows)
    assert float(rows[0]["node_hours_per_sim_day"]) > 0
    assert {t["run"] for t in read_csv(out / "timers.csv")} == {r["name"] for r in rows}


def test_format_and_no_csv(study, tmp_path):
    out = tmp_path / "out"
    viz.main([str(study), "--save", str(out), "--format", "svg", "--no-csv"])
    assert (out / "scaling.svg").read_text().lstrip().startswith(("<?xml", "<svg"))
    assert not list(out.glob("*.csv")) and not list(out.glob("*.png"))


def test_same_size_runs_are_a_comparison_not_scaling(tmp_path):
    root = tmp_path / "io"
    for name, kw in (("with_out", {}), ("less_out", {"outputs": 1})):
        logsynth.write_experiment(root, name, 2, **kw)
    out = tmp_path / "out"
    viz.main([str(root), "--save", str(out)])
    assert not (out / "scaling.png").exists()
    assert png_ok(out / "breakdown.png") and png_ok(out / "quality.png")


def test_two_series_get_a_legend_and_distinct_colors(tmp_path):
    root = tmp_path / "mix"
    for n in (1, 2, 4):
        logsynth.write_experiment(root, f"nat{n}", n, profile="native")
        logsynth.write_experiment(root, f"sca{n}", n, profile="scalasca")
    runs = analysis.load_runs([root])
    import matplotlib.pyplot as plt
    figs = viz.build_figures(plt, runs)
    assert set(figs) == {"scaling", "breakdown", "quality"}
    legend = figs["scaling"].axes[0].get_legend()
    assert legend is not None and len(legend.get_texts()) == 2
    assert any("mixed" in t.get_text() for t in figs["scaling"].texts)


def test_bars_are_clustered_by_rank_count_with_one_bar_per_series(tmp_path):
    root = tmp_path / "tpn"
    for n in (16, 32, 64):
        for tpn in (96, 192):
            logsynth.write_experiment(root, f"n{n}x{tpn}", n, tasks_per_node=tpn)
    runs = analysis.load_runs([root])
    lay = viz._Layout(runs)
    assert lay.grouped and lay.tags == ["96/node", "192/node"]    # numeric order, not "192" < "96"
    assert lay.slots == [1536, 3072, 6144, 12288]
    by_ranks = {}
    for r, x in zip(runs, lay.xs):
        by_ranks.setdefault(r.ntasks, []).append(x)
    assert len(by_ranks[3072]) == 2 and len(set(by_ranks[3072])) == 2   # 16x192 and 32x96
    assert all(int(x) == lay.slots.index(k) or abs(x - lay.slots.index(k)) < 0.5
               for k, xs in by_ranks.items() for x in xs)
    import matplotlib.pyplot as plt
    figs = viz.build_figures(plt, runs)
    thr = figs["quality"].axes[2]                                 # throughput
    assert [t.get_text() for t in thr.get_legend().get_texts()] == ["96/node", "192/node"]
    assert [t.get_text() for t in thr.get_xticklabels()] == ["1536", "3072", "6144", "12288"]


def test_repeated_rank_counts_fall_back_to_one_bar_per_run(tmp_path):
    root = tmp_path / "rep"
    for name in ("a", "b"):
        logsynth.write_experiment(root, name, 1, outputs=1 if name == "b" else 12)
    lay = viz._Layout(analysis.load_runs([root]))
    assert not lay.grouped and lay.xs == [0, 1]


def test_incomplete_runs_do_not_break_the_figures(tmp_path):
    root = tmp_path / "bad"
    logsynth.write_experiment(root, "good", 1)
    logsynth.write_experiment(root, "crashed", 2, complete=False)
    out = tmp_path / "out"
    viz.main([str(root), "--save", str(out)])
    assert png_ok(out / "breakdown.png") and png_ok(out / "quality.png")
    rows = {r["name"]: r for r in read_csv(out / "results.csv")}
    assert rows["crashed"]["status"] == "incomplete" and rows["crashed"]["wall_s"] == ""
    # and a lone crashed run still plots
    out2 = tmp_path / "out2"
    viz.main([str(root / "crashed"), "--save", str(out2)])
    assert png_ok(out2 / "overview.png") and png_ok(out2 / "components.png")


def test_x_nodes_and_fallback(study, tmp_path, capsys):
    viz.main([str(study), "--save", str(tmp_path / "a"), "--x", "nodes", "--no-csv"])
    assert png_ok(tmp_path / "a" / "scaling.png")
    bare = tmp_path / "bare"
    for n in (1, 2):
        logsynth.write_experiment(bare, f"b{n}", n, provenance=False)
    capsys.readouterr()
    viz.main([str(bare), "--save", str(tmp_path / "b"), "--x", "nodes", "--no-csv"])
    assert "--x nodes needs provenance.yaml" in capsys.readouterr().out
    assert png_ok(tmp_path / "b" / "scaling.png")


def test_no_display_without_save_explains_itself(study):
    with pytest.raises(SystemExit) as e:
        viz.main([str(study)])
    assert "--save" in str(e.value) and "display" in str(e.value)


def test_default_is_interactive_windows(study, monkeypatch):
    import matplotlib.pyplot as plt
    shown = []
    monkeypatch.setattr(viz, "NON_INTERACTIVE", set())     # pretend a display exists
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(len(plt.get_fignums())))
    viz.main([str(study)])
    assert shown == [3]                                    # one show() with all three figures


def test_missing_matplotlib_still_writes_csv(study, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib", None)   # `import matplotlib` now fails
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as e:
        viz.main([str(study), "--save", str(out)])
    assert "matplotlib" in str(e.value)
    assert len(read_csv(out / "results.csv")) == 3


def test_bad_paths_exit_cleanly(tmp_path):
    with pytest.raises(SystemExit) as e:
        viz.main([str(tmp_path / "nope")])
    assert "no such file" in str(e.value)
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit) as e:
        viz.main([str(tmp_path / "empty")])
    assert "No log.atmosphere.0000.out" in str(e.value)


def test_summary_table_lists_runs_and_notes(study, capsys):
    viz.print_summary(analysis.load_runs([study]))
    text = capsys.readouterr().out
    assert "60km_native_n4x96" in text and "sim d/d" in text
    assert "note:" not in text                            # all complete, all native


def test_x_nodes_clusters_bars_by_node_count(tmp_path):
    root = tmp_path / "tpn"
    for n in (16, 22, 32):
        for tpn in (96, 192):
            logsynth.write_experiment(root, f"n{n}x{tpn}", n, tasks_per_node=tpn)
    runs = analysis.load_runs([root])
    by_ranks = viz._Layout(runs)
    by_nodes = viz._Layout(runs, "nodes")
    assert by_ranks.slots == [1536, 2112, 3072, 4224, 6144]       # 16x192 and 32x96 share 3072
    assert by_nodes.grouped and by_nodes.slots == [16, 22, 32]
    assert by_nodes.xlabel == "nodes" and by_ranks.xlabel == "MPI ranks"
    assert len(by_nodes.slots) == 3 and len({round(x) for x in by_nodes.xs}) == 3
    import matplotlib.pyplot as plt
    figs = viz.build_figures(plt, runs, "nodes")
    for name, ax_index in (("breakdown", 0), ("breakdown", 1), ("quality", 1), ("quality", 2),
                           ("quality", 3)):
        ax = figs[name].axes[ax_index]
        assert [t.get_text() for t in ax.get_xticklabels()] == ["16", "22", "32"], (name, ax_index)
        assert ax.get_xlabel() == "nodes"


def test_x_nodes_labels_single_series_by_node_count(study):
    runs = analysis.load_runs([study])
    assert viz._Layout(runs, "nodes").labels == ["1", "2", "4"]
    assert viz._Layout(runs).labels == ["96", "192", "384"]


def _stack_totals(ax, n):
    """Height of each of the n stacked bars (bar i is centered on x = i)."""
    return [sum(p.get_height() for p in ax.patches if round(p.get_x() + p.get_width() / 2) == i)
            for i in range(n)]


def test_breakdown_bars_each_add_to_100_percent(study):
    import matplotlib.pyplot as plt
    runs = analysis.load_runs([study])
    fig = viz.build_figures(plt, runs)["breakdown"]
    assert len(fig.axes) == 3
    for ax in fig.axes:
        assert ax.get_ylabel() == "% of total run time"
        assert _stack_totals(ax, len(runs)) == pytest.approx([100.0] * len(runs))
    legend = lambda ax: [t.get_text() for t in ax.get_legend().get_texts()]   # noqa: E731
    assert legend(fig.axes[1])[-1] == "outside time integration"
    assert legend(fig.axes[2])[-1] == "outside physics driver"


def test_breakdown_shows_every_component_and_physics_scheme(tmp_path):
    root = tmp_path / "real"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        (root / name / "log.atmosphere.0000.out").write_text(SAMPLE.read_text())
    import matplotlib.pyplot as plt
    runs = analysis.load_runs([root])
    fig = viz.build_figures(plt, runs)["breakdown"]
    legend = lambda ax: [t.get_text() for t in ax.get_legend().get_texts()]   # noqa: E731
    log = runs[0].log
    integ = [c.name for c in log.children("time integration")]
    physics = [c.name for c in log.children("physics driver")]
    assert len(integ) > 8 and len(physics) >= 8              # more than the palette has colors
    assert sorted(legend(fig.axes[1])[:-2]) == sorted(integ)
    assert sorted(legend(fig.axes[2])[:-2]) == sorted(physics)
    # slices beyond the palette are told apart by a hatch pattern
    hatches = {p.get_hatch() for p in fig.axes[1].patches}
    assert None in hatches and len(hatches) > 1
    for ax in fig.axes[1:]:
        assert _stack_totals(ax, len(runs)) == pytest.approx([100.0] * len(runs))


def test_shares_cover_the_whole_run():
    shares, scale = viz._shares(100.0, {"a": 30.0, "b": 20.0, "none": None})
    assert scale == 1.0 and shares == {"a": 30.0, "b": 20.0, viz.REST: 50.0}
    # parts that overshoot the whole (maxima over ranks) are scaled down to still sum to 100
    shares, scale = viz._shares(100.0, {"a": 70.0, "b": 40.0, "neg": -5.0})
    assert scale == pytest.approx(1.1) and sum(shares.values()) == pytest.approx(100.0)
    assert shares[viz.REST] == 0.0 and "neg" not in shares
    assert viz._shares(None, {"a": 1.0}) == ({}, 1.0)


def test_imbalance_heatmap_columns_follow_the_bar_clusters(tmp_path):
    root = tmp_path / "tpn"
    for n in (16, 32):
        for tpn in (96, 192):
            logsynth.write_experiment(root, f"n{n}x{tpn}", n, tasks_per_node=tpn)
    import matplotlib.pyplot as plt
    figs = viz.build_figures(plt, analysis.load_runs([root]), "nodes")
    labels = [t.get_text() for t in figs["quality"].axes[0].get_xticklabels()]
    assert labels == ["16 · 96/node", "16 · 192/node", "32 · 96/node", "32 · 192/node"]
