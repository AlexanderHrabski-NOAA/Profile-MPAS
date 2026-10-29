from mpas_profile import config, render


def exp(**kw):
    spec = {"resolution": "60km", "nodes": 4, "tasks_per_node": 96, **kw}
    return config.build_experiment(spec, "test")


def test_modulefile_wrappers():
    assert 'setenv("FC", "scorep-mpiifx")' in render.modulefile(True)
    assert 'setenv("FC", "mpiifx")' in render.modulefile(False)
    assert "scorep" not in render.modulefile(False)


def test_job_profile_vs_normal():
    prof = render.job(exp(profile=True), "/b/mpas_atmosphere")
    norm = render.job(exp(profile=False), "/b/mpas_atmosphere")
    assert "scalasca --analyze srun -N 4 --tasks-per-node=96" in prof
    assert "SCOREP_FILTERING_FILE" in prof
    assert "scalasca" not in norm and "SCOREP" not in norm
    assert "\nsrun -N 4 --tasks-per-node=96 --cpus-per-task=1 /b/mpas_atmosphere\n" in norm


def test_job_ntasks_form():
    e = config.build_experiment({"resolution": "60km", "nodes": 2, "ntasks": 100}, "t")
    job = render.job(e, "/b/x")
    assert "#SBATCH --ntasks=100" in job and "--tasks-per-node" not in job
    assert "srun -N 2 -n 100 " in job


def test_namelist_and_streams():
    res = config.load_resolutions()["60km"]
    nl = render.namelist(exp(start_time="2026-2-1_00:00:00"), res)
    assert "config_start_time = '2026-2-1_00:00:00'" in nl
    assert "config_block_decomp_file_prefix = 'global.60km.graph.info.part.'" in nl
    st = render.streams(res)
    assert 'filename_template="global.60km.init.nc"' in st
    assert "x1.40962.sfc_update.nc" in st
