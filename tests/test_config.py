import pytest
import yaml

from mpas_profile import SetupError, config


def load(tmp_path, doc):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(doc))
    return config.load_study(p)


def test_sweep_expands_and_names(tmp_path):
    s = load(tmp_path, {
        "study": "t",
        "defaults": {"resolution": "60km", "tasks_per_node": 96,
                     "mpas": {"ref": "v1.0"}},
        "experiments": [{"sweep": {"nodes": [1, 2, 4]}}],
    })
    assert [e.name for e in s.experiments] == [
        "60km_v1.0_prof_n1x96", "60km_v1.0_prof_n2x96", "60km_v1.0_prof_n4x96"]
    assert [e.ntasks for e in s.experiments] == [96, 192, 384]


def test_ntasks_form(tmp_path):
    s = load(tmp_path, {"study": "t", "defaults": {"resolution": "60km"},
                        "experiments": [{"nodes": 2, "ntasks": 100}]})
    e = s.experiments[0]
    assert (e.ntasks, e.tasks_per_node, e.name) == (100, None, "60km_pinned_prof_n2t100")


def test_entry_overrides_defaults_and_dotted_sweep(tmp_path):
    s = load(tmp_path, {
        "study": "t",
        "defaults": {"resolution": "60km", "tasks_per_node": 96, "nodes": 1,
                     "mpas": {"ref": "a"}},
        "experiments": [{"profile": False, "sweep": {"mpas.ref": ["a", "b"]}}],
    })
    assert [(e.mpas_ref, e.profile) for e in s.experiments] == [("a", "native"), ("b", "native")]


@pytest.mark.parametrize("value, mode, scorep, tag", [
    ("scalasca", "scalasca", True, "prof"),
    ("native", "native", False, "native"),
    (True, "scalasca", True, "prof"),      # old boolean form
    (False, "native", False, "native"),
])
def test_profile_modes(value, mode, scorep, tag):
    e = config.build_experiment(
        {"resolution": "60km", "nodes": 2, "tasks_per_node": 96, "profile": value}, "t")
    assert (e.profile, e.scorep, e.tag) == (mode, scorep, tag)
    assert f"_{tag}_n2x96" in e.name


def test_profile_default_is_scalasca():
    e = config.build_experiment({"resolution": "60km", "nodes": 1, "tasks_per_node": 96}, "t")
    assert e.profile == "scalasca" and e.name.endswith("_prof_n1x96")


@pytest.mark.parametrize("bad", ["yes", "Native", "prof", 1, None, ["native"]])
def test_profile_rejects_other_values(bad):
    with pytest.raises(SetupError, match="'profile' must be one of scalasca, native"):
        config.build_experiment(
            {"resolution": "60km", "nodes": 1, "tasks_per_node": 96, "profile": bad}, "t")


def test_sweep_is_cartesian(tmp_path):
    s = load(tmp_path, {
        "study": "t", "defaults": {"resolution": "60km"},
        "experiments": [{"sweep": {"nodes": [1, 2], "tasks_per_node": [48, 96]}}]})
    assert len(s.experiments) == 4


def zip_study(entry):
    return {"study": "t", "defaults": {"resolution": "60km"}, "experiments": [entry]}


def test_zip_is_pairwise(tmp_path):
    s = load(tmp_path, zip_study({"zip": {"nodes": [1, 2, 4], "tasks_per_node": [96, 48, 24]}}))
    assert [(e.nodes, e.tasks_per_node) for e in s.experiments] == [(1, 96), (2, 48), (4, 24)]


def test_zip_crossed_with_sweep(tmp_path):
    s = load(tmp_path, zip_study({
        "sweep": {"output": [True, False]},
        "zip": {"nodes": [1, 2, 4], "pio.iotasks_per_node": [1, 2, 4]},
        "tasks_per_node": 96}))
    assert len(s.experiments) == 6
    assert len({e.name for e in s.experiments}) == 6
    assert {(e.nodes, e.pio_iotasks_per_node) for e in s.experiments} == {(1, 1), (2, 2), (4, 4)}
    assert sum(not e.output for e in s.experiments) == 3


def test_zip_dotted_key(tmp_path):
    s = load(tmp_path, zip_study({"nodes": 1, "tasks_per_node": 96,
                                  "zip": {"mpas.ref": ["a", "b"], "output": [True, False]}}))
    assert [(e.mpas_ref, e.output) for e in s.experiments] == [("a", True), ("b", False)]


@pytest.mark.parametrize("entry, msg", [
    ({"zip": {"nodes": [1, 2], "tasks_per_node": [96]}}, "same length: 'nodes' has 2, 'tasks_per_node' has 1"),
    ({"zip": {"nodes": []}}, "zip 'nodes' must be a non-empty list"),
    ({"zip": {"nodes": 2}}, "zip 'nodes' must be a non-empty list"),
    ({"zip": [1, 2]}, "'zip' must be a mapping"),
    ({"zip": {}}, "'zip' must be a mapping"),
    ({"sweep": {"nodes": [1, 2]}, "zip": {"nodes": [1, 2]}}, "both 'sweep' and 'zip': nodes"),
])
def test_zip_errors(tmp_path, entry, msg):
    with pytest.raises(SetupError, match=msg):
        load(tmp_path, zip_study({"tasks_per_node": 96, **entry}))


@pytest.mark.parametrize("entry, msg", [
    ({"nodes": 1}, "exactly one"),
    ({"nodes": 1, "tasks_per_node": 2, "ntasks": 2}, "exactly one"),
    ({"nodes": 4, "ntasks": 2}, "less than nodes"),
    ({"tasks_per_node": 2}, "'nodes'"),
    ({"nodes": 0, "tasks_per_node": 2}, "'nodes'"),
    ({"nodes": 1, "tasks_per_node": 2, "bogus": 1}, "unknown key"),
    ({"nodes": 1, "tasks_per_node": 2, "mpas": {"profile": True}}, "'mpas' may only"),
    ({"nodes": 1, "tasks_per_node": 2, "profile": "yes"}, "'profile'"),
    ({"nodes": 1, "tasks_per_node": 2, "output": "yes"}, "'output' must be true or false"),
])
def test_validation_errors(tmp_path, entry, msg):
    with pytest.raises(SetupError, match=msg):
        load(tmp_path, {"study": "t", "defaults": {"resolution": "60km"},
                        "experiments": [entry]})


def test_io_defaults_keep_names():
    e = config.build_experiment({"resolution": "60km", "nodes": 2, "tasks_per_node": 96}, "t")
    assert (e.output, e.pio_num_iotasks, e.pio_stride, e.pio_io_type) == (True, 0, 1, None)
    assert e.name == "60km_pinned_prof_n2x96"


def test_iotasks_per_node_zero_means_all_ranks():
    e = config.build_experiment({"resolution": "60km", "nodes": 2, "tasks_per_node": 96,
                                 "pio": {"iotasks_per_node": 0}}, "t")
    assert (e.pio_num_iotasks, e.pio_stride, e.pio_iotasks_per_node) == (0, 1, None)
    assert e.name == "60km_pinned_prof_n2x96"


def test_iotasks_per_node_derives_stride():
    e = config.build_experiment({"resolution": "60km", "nodes": 4, "tasks_per_node": 96,
                                 "pio": {"iotasks_per_node": 2}}, "t")
    assert (e.pio_num_iotasks, e.pio_stride) == (8, 48)
    assert e.name.endswith("_io2pn")
    # ntasks form: ranks per node comes from ntasks / nodes
    e = config.build_experiment({"resolution": "60km", "nodes": 2, "ntasks": 24,
                                 "pio": {"iotasks_per_node": 5}}, "t")
    assert (e.pio_num_iotasks, e.pio_stride) == (10, 2)   # 12 per node // 5


def test_io_names_and_sweep(tmp_path):
    s = load(tmp_path, {
        "study": "t",
        "defaults": {"resolution": "60km", "tasks_per_node": 96, "nodes": 2},
        "experiments": [{"sweep": {"pio.iotasks_per_node": [1, 2], "output": [True, False]}}]})
    names = [e.name for e in s.experiments]
    assert len(set(names)) == 4
    assert sum("_noout_" in n for n in names) == 2
    assert sum(n.endswith("_io1pn") for n in names) == 2


@pytest.mark.parametrize("entry, msg", [
    ({"output": "no"}, "'output'"),
    ({"pio": {"bogus": 1}}, "'pio' may only"),
    ({"pio": {"io_type": "hdf"}}, "io_type"),
    ({"pio": {"num_iotasks": -1}}, "num_iotasks"),
    ({"pio": {"num_iotasks": 500}}, "exceeds the rank count"),
    ({"pio": {"num_iotasks": 8, "stride": 20}}, "exceeds the rank count"),
    ({"pio": {"stride": 0}}, "'stride'"),
    ({"pio": {"iotasks_per_node": -1}}, "iotasks_per_node"),
    ({"pio": {"iotasks_per_node": 97}}, "exceeds the 96 ranks per node"),
    ({"pio": {"iotasks_per_node": 2, "stride": 4}}, "cannot be combined"),
    ({"pio": {"iotasks_per_node": 2, "num_iotasks": 4}}, "cannot be combined"),
])
def test_io_validation_errors(entry, msg):
    spec = {"resolution": "60km", "nodes": 1, "tasks_per_node": 96, **entry}
    with pytest.raises(SetupError, match=msg):
        config.build_experiment(spec, "t")


def test_iotasks_per_node_needs_even_ranks():
    with pytest.raises(SetupError, match="not divisible"):
        config.build_experiment({"resolution": "60km", "nodes": 3, "ntasks": 100,
                                 "pio": {"iotasks_per_node": 1}}, "t")


def test_duplicate_names(tmp_path):
    with pytest.raises(SetupError, match="Duplicate"):
        load(tmp_path, {"study": "t", "defaults": {"resolution": "60km"},
                        "experiments": [{"nodes": 1, "ntasks": 4}, {"nodes": 1, "ntasks": 4}]})


def test_missing_resolution(tmp_path):
    with pytest.raises(SetupError, match="'resolution'"):
        load(tmp_path, {"study": "t", "experiments": [{"nodes": 1, "ntasks": 4}]})


def test_resolutions_registry():
    r = config.load_resolutions()["60km"]
    assert r.partition_prefix == "global.60km.graph.info.part."
