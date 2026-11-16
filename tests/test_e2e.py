"""Run a copy of the tool against a fake MPAS repo with stubbed cluster commands."""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

SRC = Path(__file__).resolve().parent.parent

STUBS = {
    "sbatch": '#!/bin/bash\necho "sbatch $@ (in $PWD)" >> "$STUB_LOG"\necho 4242$RANDOM\n',
    # Mimics gpmetis: writes <graph>.part.<n> into the cwd.
    "gpmetis": '#!/bin/bash\necho "gpmetis $@" >> "$STUB_LOG"\n'
               'for last; do :; done\nfor g; do :; done\n'
               'graph=${@: -2:1}\necho 0 > "$graph.part.$last"\n',
}
# MPI wrappers that report the compiler they call, for the build-time compiler check.
WRAPPER = ('#!/bin/bash\ncase "$0" in *mpifort|*mpiifx) echo "${STUB_FC:-ifx} -I/x -lmpi";; '
           '*mpic++|*mpiicpx) echo "icpx -I/x";; *) echo "icx -I/x";; esac\n')
STUBS.update({name: WRAPPER for name in
              ("mpicc", "mpic++", "mpifort", "mpiicx", "mpiicpx", "mpiifx")})
MODULE_FN = '() { echo "module $*" >> "$STUB_LOG"; if [ "$1" = load ]; then export MODLOADED=$2; fi; }'


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "profile"
    root.mkdir()
    for item in ["build-and-run.py", "mpas_profile", "templates", "config", "utils"]:
        s = SRC / item
        (shutil.copytree if s.is_dir() else shutil.copy)(
            s, root / item, **({"ignore": shutil.ignore_patterns("__pycache__", "GKlib", "METIS")}
                               if s.is_dir() else {}))
    # data
    for d, f in [("ic", "global.60km.init.nc"), ("fix", "RRTMG_LW_DATA")]:
        (root / d / "60km").mkdir(parents=True)
        (root / d / "60km" / f).write_text("x")
    (root / "partition" / "60km").mkdir(parents=True)
    (root / "partition" / "60km" / "global.60km.graph.info").write_text("graph")
    (root / "partition" / "60km" / "global.60km.graph.info.part.192").write_text("pre")
    # fake MPAS repo
    mpas = tmp_path / "mpas-src"
    mpas.mkdir()
    (mpas / "build.sh").write_text(textwrap.dedent("""\
        #!/bin/bash
        echo "build.sh MODLOADED=$MODLOADED in $PWD" >> "$STUB_LOG"
        test -f modulefiles/mpas/${EXPECT_MODULE:-ursa.intel}.lua || exit 9
        test -f ext/physics.F90 || { echo "nested submodule missing" >> "$STUB_LOG"; exit 8; }
        [ -n "$FAIL_BUILD" ] && exit 3
        mkdir -p build/bin && echo bin > build/bin/mpas_atmosphere
        """))
    (mpas / "build.sh").chmod(0o755)
    run = lambda *a, cwd=mpas: subprocess.run(a, cwd=cwd, check=True, capture_output=True)
    # a nested submodule, as MPAS has for its physics packages
    ext = tmp_path / "ext-src"
    ext.mkdir()
    (ext / "physics.F90").write_text("! physics\n")
    run("git", "init", "-q", "-b", "main", cwd=ext)
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=ext)
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "ext", cwd=ext)
    run("git", "init", "-q", "-b", "main")
    run("git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(ext), "ext")
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
    run("git", "tag", "v1")
    # the tool is run from a git repo so provenance can record its commit
    run("git", "init", "-q", cwd=root)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in STUBS.items():
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    log = tmp_path / "stub.log"
    log.write_text("")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", STUB_LOG=str(log),
               # recent git refuses file:// submodule transport by default
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="protocol.file.allow",
               GIT_CONFIG_VALUE_0="always")
    env["BASH_FUNC_module%%"] = MODULE_FN

    def go(study, *flags, env_extra=None):
        p = root / "study.yaml"
        p.write_text(yaml.safe_dump(study))
        return subprocess.run(["python3", "build-and-run.py", str(p), *flags], cwd=root,
                              env={**env, **(env_extra or {})}, capture_output=True, text=True)

    return type("W", (), dict(root=root, mpas=mpas, log=log, go=go))


def study(mpas, nodes=(1, 2, 4), **defaults):
    return {"study": "t", "defaults": {"resolution": "60km", "tasks_per_node": 96,
                                       "mpas": {"ref": "v1", "repo": str(mpas)}, **defaults},
            "experiments": [{"sweep": {"nodes": list(nodes)}}]}


def test_sweep_end_to_end(world):
    r = world.go(study(world.mpas))
    assert r.returncode == 0, r.stdout + r.stderr
    log = world.log.read_text()
    assert log.count("build.sh") == 1                       # one shared build
    assert "mpiifx -> ifx" in r.stdout                      # Ursa's compiler check ran
    assert log.count("sbatch") == 3
    assert log.count("gpmetis") == 2                        # 96 and 384; 192 pre-existed
    assert "MODLOADED=mpas/ursa.intel" in log

    exps = world.root / "runs" / "t" / "experiments"
    assert sorted(p.name for p in exps.iterdir()) == [
        "60km_v1_prof_n1x96", "60km_v1_prof_n2x96", "60km_v1_prof_n4x96"]
    e = exps / "60km_v1_prof_n4x96"
    assert (e / "global.60km.graph.info.part.384").is_symlink()
    assert (e / "global.60km.init.nc").is_symlink()
    assert (e / "scorep.filt").exists() and (e / "stream_list.atmosphere.output").exists()
    assert (e / "modulefiles/mpas/ursa.intel.lua").exists()
    job = (e / "job.fcst").read_text()
    build_dir = next((world.root / "runs/t/builds").iterdir())
    assert str(build_dir / "build/bin/mpas_atmosphere") in job
    prov = yaml.safe_load((e / "provenance.yaml").read_text())
    assert len(prov["mpas"]["commit"]) == 40 and prov["jobid"].startswith("4242")
    assert prov["gpmetis"].startswith("gpmetis -minconn -contig -niter=200")
    # the clone is not re-pointed at anything else
    origin = subprocess.run(["git", "-C", str(build_dir), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    assert origin == str(world.mpas)
    pdir = world.root / "partition" / "60km"
    assert sorted(p.name for p in pdir.iterdir()) == sorted(
        ["global.60km.graph.info"] + [f"global.60km.graph.info.part.{n}" for n in (96, 192, 384)])
    assert (pdir / "global.60km.graph.info.part.192").read_text() == "pre"   # untouched


def test_single_rank_needs_no_partition(world):
    s = study(world.mpas, nodes=(1,))
    s["defaults"].pop("tasks_per_node")
    s["experiments"] = [{"nodes": 1, "ntasks": 1}]
    r = world.go(s, "--no-submit")
    assert r.returncode == 0, r.stderr
    assert "gpmetis" not in world.log.read_text()
    assert not list((world.root / "runs/t/experiments").rglob("*part*"))


def test_rerun_requires_overwrite_and_reuses_build(world):
    assert world.go(study(world.mpas)).returncode == 0
    r = world.go(study(world.mpas))
    assert r.returncode != 0 and "already exist" in r.stderr
    world.log.write_text("")
    r = world.go(study(world.mpas), "-o")
    assert r.returncode == 0, r.stderr
    log = world.log.read_text()
    assert "build.sh" not in log and log.count("sbatch") == 3


def test_failed_build_submits_nothing(world):
    r = world.go(study(world.mpas), env_extra={"FAIL_BUILD": "1"})
    assert r.returncode != 0
    assert "sbatch" not in world.log.read_text()
    assert not (world.root / "runs/t/experiments").exists()


def test_dry_run_touches_nothing(world):
    r = world.go(study(world.mpas), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert not (world.root / "runs").exists()
    assert world.log.read_text() == ""


def test_native_and_bad_ref(world):
    r = world.go(study(world.mpas, nodes=(2,), profile="native"), "--no-submit")
    assert r.returncode == 0, r.stderr
    assert next((world.root / "runs/t/builds").iterdir()).name.endswith("-native")
    e = world.root / "runs/t/experiments/60km_v1_native_n2x96"
    assert not (e / "scorep.filt").exists()
    assert "scorep" not in (e / "modulefiles/mpas/ursa.intel.lua").read_text()
    s = study(world.mpas)
    s["defaults"]["mpas"]["ref"] = "nope"
    r = world.go(s, "--dry-run")
    assert r.returncode != 0 and "Cannot resolve MPAS ref" in r.stderr


def gaea_fs(world, prefix=None):
    """Point gaeac6's compute_fs at the test tree (it is /gpfs/f6 on the real machine)."""
    p = world.root / "config" / "machines.yaml"
    machines = yaml.safe_load(p.read_text())
    machines["gaeac6"]["compute_fs"] = str(prefix or world.root.parent.resolve())
    p.write_text(yaml.safe_dump(machines))


def test_gaeac6_end_to_end(world):
    gaea_fs(world)
    s = study(world.mpas, nodes=(2,), machine="gaeac6", account="acct", profile="native",
              tasks_per_node=192)
    r = world.go(s, env_extra={"EXPECT_MODULE": "gaeac6.intel"})
    assert r.returncode == 0, r.stdout + r.stderr
    log = world.log.read_text()
    assert "MODLOADED=mpas/gaeac6.intel" in log and log.count("sbatch") == 1
    assert "mpifort -> ifx" in r.stdout and "mpicc -> icx" in r.stdout
    assert next((world.root / "runs/t/builds").iterdir()).name.endswith("-native-gaeac6")
    e = world.root / "runs/t/experiments/60km_v1_native_n2x192_gaeac6"
    assert (e / "modulefiles/mpas/gaeac6.intel.lua").exists()
    assert not (e / "modulefiles/mpas/ursa.intel.lua").exists()
    job = (e / "job.fcst").read_text()
    assert "--clusters=c6" in job and "module load mpas/gaeac6.intel" in job
    prov = yaml.safe_load((e / "provenance.yaml").read_text())
    assert prov["experiment"]["machine"] == "gaeac6"
    assert prov["jobid"].startswith("4242")   # `sbatch --parsable` prints "<id>;c6" on Gaea


def test_machines_get_separate_builds(world):
    gaea_fs(world)
    s = study(world.mpas, nodes=(1,), profile="native", account="acct")
    s["experiments"] = [{"nodes": 1, "machine": "ursa"},
                        {"nodes": 1, "machine": "gaeac6", "tasks_per_node": 192}]
    r = world.go(s, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Builds: 2" in r.stdout


def test_gaeac6_refuses_paths_off_the_compute_file_system(world):
    s = study(world.mpas, nodes=(1,), machine="gaeac6", account="acct", profile="native",
              tasks_per_node=192)
    r = world.go(s)    # the tree is not under /gpfs/f6
    assert r.returncode != 0 and "only mount /gpfs/f6" in r.stderr
    assert "sbatch" not in world.log.read_text() and not (world.root / "runs").exists()
    # a data directory that links off the file system is caught even when the repo is fine
    elsewhere = world.root.parent / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "f").write_text("x")
    shutil.rmtree(world.root / "fix" / "60km")
    (world.root / "fix" / "60km").symlink_to(elsewhere)
    gaea_fs(world, world.root.resolve())
    r = world.go(s, "--dry-run")
    assert r.returncode != 0 and "elsewhere" in r.stderr


def test_gaeac6_build_refuses_a_wrapper_that_is_not_ifx(world):
    gaea_fs(world)
    s = study(world.mpas, nodes=(1,), machine="gaeac6", account="acct", profile="native",
              tasks_per_node=192)
    r = world.go(s, env_extra={"EXPECT_MODULE": "gaeac6.intel", "STUB_FC": "ifort"})
    assert r.returncode != 0 and "'mpifort' does not call ifx" in r.stderr
    log = world.log.read_text()
    assert "build.sh" not in log and "sbatch" not in log


def test_ursa_build_refuses_a_wrapper_that_is_not_ifx(world):
    r = world.go(study(world.mpas), env_extra={"STUB_FC": "ifort"})
    assert r.returncode != 0 and "'mpiifx' does not call ifx" in r.stderr
    log = world.log.read_text()
    assert "build.sh" not in log and "sbatch" not in log
