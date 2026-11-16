from mpas_profile import config, render


def exp(**kw):
    spec = {"resolution": "60km", "nodes": 4, "tasks_per_node": 96, **kw}
    return config.build_experiment(spec, "test")


def test_modulefile_wrappers():
    assert 'setenv("FC", "scorep-mpiifx")' in render.modulefile(True)
    assert 'setenv("FC", "mpiifx")' in render.modulefile(False)
    assert "scorep" not in render.modulefile(False)


def test_job_scalasca_vs_native():
    prof = render.job(exp(profile="scalasca"), "/b/mpas_atmosphere")
    norm = render.job(exp(profile="native"), "/b/mpas_atmosphere")
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
    st = render.streams(exp(), res)
    assert 'filename_template="global.60km.init.nc"' in st
    assert "x1.40962.sfc_update.nc" in st


def test_io_defaults_unchanged():
    res = config.load_resolutions()["60km"]
    e = exp()
    nl, st = render.namelist(e, res), render.streams(e, res)
    assert "config_pio_num_iotasks = 0\n    config_pio_stride = 1\n" in nl
    assert 'output_interval="1_00:00:00" />' in st
    assert st.count('output_interval="3:00:00" >') == 2
    assert "io_type" not in st and 'output_interval="none"' not in st


def test_output_off():
    res = config.load_resolutions()["60km"]
    st = render.streams(exp(output=False), res)
    assert st.count('output_interval="none"') == 3
    assert 'output_interval="1_00:00:00"' not in st and 'output_interval="3:00:00"' not in st
    assert 'input_interval="3:00:00"' in st         # inputs untouched (lbc_in)


def test_pio_namelist_and_io_type():
    res = config.load_resolutions()["60km"]
    e = exp(pio={"num_iotasks": 8, "stride": 48, "io_type": "pnetcdf,cdf5"})
    assert "config_pio_num_iotasks = 8\n    config_pio_stride = 48\n" in render.namelist(e, res)
    st = render.streams(e, res)
    assert st.count('io_type="pnetcdf,cdf5"') == 3   # restart, output, diagnostics only


def gaea(**kw):
    return exp(machine="gaeac6", account="acct", profile="native", tasks_per_node=192, **kw)


def test_gaeac6_job_card():
    job = render.job(gaea(), "/b/mpas_atmosphere")
    assert "#SBATCH --account=acct\n#SBATCH --qos=normal\n" in job
    assert "#SBATCH --clusters=c6\n#SBATCH --partition=batch\n" in job
    assert "MACHINE_ID=gaeac6" in job and "module load mpas/gaeac6.intel" in job
    assert "I_MPI" not in job and "scalasca" not in job
    assert ("\nsrun -N 4 --tasks-per-node=192 --cpus-per-task=1 "
            "--hint=nomultithread --cpu-bind=cores /b/mpas_atmosphere\n") in job


def test_ursa_job_card_has_no_extra_directives():
    job = render.job(exp(), "/b/x")
    assert "--clusters" not in job and "MACHINE_ID=ursa" in job
    assert "module load mpas/ursa.intel" in job and "I_MPI_PIN=1" in job


def test_gaeac6_modulefile():
    mod = render.modulefile(False, "gaeac6")
    assert 'load("stack-cray-mpich/8.1.32")' in mod
    assert "spack-stack-2.0.0/envs/ue-oneapi-2025.2.1/modules/Core" in mod
    for m in ("stack-intel-oneapi-compilers/2025.2.1", "cmake/3.31.8",
              "parallel-netcdf/1.12.3", "parallelio/2.6.2"):
        assert f'load("{m}")' in mod
    assert "mkl" not in mod and "2024" not in mod
    assert 'setenv("CMAKE_Fortran_COMPILER", "mpifort")' in mod and "scorep" not in mod
    # oneAPI LLVM compilers only, selected through the Cray MPICH wrapper variables
    for var, comp in [("MPICH_CC", "icx"), ("MPICH_CXX", "icpx"), ("MPICH_FC", "ifx")]:
        assert f'setenv("{var}", "{comp}")' in mod
    assert '"ifort"' not in mod and '"icc"' not in mod and '"icpc"' not in mod


def test_gaeac6_modulefile_with_scalasca(monkeypatch):
    machines = config.load_machines()
    m = machines["gaeac6"]
    machines["gaeac6"] = type(m)(**{**vars(m), "scalasca": {
        "modulepath": "/opt/mods", "module": "scalasca/2.6.1"}})
    monkeypatch.setattr(render, "load_machines", lambda: machines)
    mod = render.modulefile(True, "gaeac6")
    assert 'prepend_path("MODULEPATH", "/opt/mods")' in mod and 'load("scalasca/2.6.1")' in mod
    assert 'setenv("CMAKE_Fortran_COMPILER", "scorep-mpifort")' in mod
