"""Command-line driver: setup YAML in, built and submitted experiments out."""

import argparse
import shutil
import subprocess
import sys


from . import REPO, SetupError, build, config, experiment, partition, render

RUNS = REPO / "runs"


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Build, partition, and submit the experiments in a setup YAML.")
    p.add_argument("setup", metavar="SETUP.yaml", help="The study setup file.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate and render everything, then print the plan. "
                        "Does not clone, build, partition, or submit.")
    p.add_argument("--no-submit", action="store_true",
                   help="Build and stage the experiments but do not call sbatch.")
    p.add_argument("--rebuild", action="store_true",
                   help="Rebuild MPAS even if a finished build exists.")
    p.add_argument("-o", "--overwrite", action="store_true",
                   help="Replace experiment directories that already exist "
                        "(builds are kept).")
    return p.parse_args(argv)


def table(rows, header):
    widths = [max(len(str(r[i])) for r in [header] + rows) for i in range(len(header))]
    for r in [header] + rows:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())


def run(argv):
    args = parse_args(argv)
    study = config.load_study(args.setup)
    resolutions = config.load_resolutions()
    study_dir = RUNS / study.name
    edirs = {e.name: study_dir / "experiments" / e.name for e in study.experiments}

    for e in study.experiments:
        if e.resolution not in resolutions:
            raise SetupError(f"{e.name}: unknown resolution '{e.resolution}' "
                             f"(known: {', '.join(resolutions)}).")
    for key in sorted({e.resolution for e in study.experiments}):
        experiment.check_data(resolutions[key])

    existing = [n for n, d in edirs.items() if d.exists()]
    if existing and not args.overwrite and not args.dry_run:
        raise SetupError("These experiment directories already exist: "
                         f"{', '.join(existing)}.\nUse -o to replace them, or "
                         "change the study or experiment names.")

    # Resolve every MPAS ref to a commit up front so typos fail before any build.
    print(f"Study '{study.name}': {len(study.experiments)} experiment(s)")
    sources = {}
    for e in study.experiments:
        sources[e.name] = build.resolve(e)
    builds = {}
    for e in study.experiments:
        source, sha = sources[e.name]
        builds.setdefault((sha, e.profile), (source, build.build_dir(study_dir, sha, e.profile)))

    print("\nExperiments:")
    table([[e.name, e.resolution, e.nodes, e.ntasks, e.tasks_per_node or "-",
            sources[e.name][1][:12], e.tag] for e in study.experiments],
          ["name", "res", "nodes", "ranks", "tpn", "mpas", "profile"])
    print(f"\nBuilds: {len(builds)}")
    for (sha, profile), (source, bdir) in builds.items():
        state = "reuse" if (bdir / build.STAMP).exists() and not args.rebuild else "build"
        print(f"  {bdir.name}  [{state}]")

    # Rendering every template now catches template errors before any build.
    for e in study.experiments:
        res = resolutions[e.resolution]
        render.namelist(e, res), render.streams(res), render.modulefile(e.profile)
        render.job(e, build.mpas_bin(build.build_dir(study_dir, sources[e.name][1], e.profile)))

    needed = sorted({(e.resolution, e.ntasks) for e in study.experiments})
    gpmetis = {}
    print("\nPartitions:")
    for key, n in needed:
        res = resolutions[key]
        cmd = partition.ensure_partition(res, n, dry_run=True)
        gpmetis[(key, n)] = cmd
        print(f"  {key} x {n}: " + ("generate" if cmd else "ok"))

    if args.dry_run:
        print("\nDry run: nothing was cloned, built, partitioned, or submitted.")
        return

    # Everything that can fail runs before the first job is submitted.
    print("\nBuilding...")
    study_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.setup, study_dir / "study.yaml")
    for (sha, profile), (source, bdir) in builds.items():
        build.ensure_build(bdir, source, sha, profile, rebuild=args.rebuild)

    print("\nPartitioning...")
    for key, n in needed:
        if gpmetis[(key, n)]:
            partition.ensure_partition(resolutions[key], n)

    print("\nStaging" + ("" if args.no_submit else " and submitting") + "...")
    for e in study.experiments:
        res = resolutions[e.resolution]
        source, sha = sources[e.name]
        bdir = build.build_dir(study_dir, sha, e.profile)
        edir = edirs[e.name]
        if edir.exists():
            shutil.rmtree(edir)
        mpas_info = {"ref": e.mpas_ref or "(submodule pin)", "commit": sha,
                     "source": str(source), "build": str(bdir)}
        prov = experiment.stage(e, res, edir, bdir, mpas_info, gpmetis[(e.resolution, e.ntasks)])
        if args.no_submit:
            print(f"  {e.name}: staged")
        else:
            prov["jobid"] = experiment.submit(edir)
            experiment.write_provenance(edir, prov)
            print(f"  {e.name}: job {prov['jobid']}")
    print(f"\nDone. Experiments are in {study_dir / 'experiments'}")


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
    except SetupError as e:
        sys.exit(f"Error: {e}")
    except subprocess.CalledProcessError as e:
        sys.exit(f"Error: command failed with exit status {e.returncode}: "
                 f"{' '.join(map(str, e.cmd[:3]))}")
    except OSError as e:
        sys.exit(f"Error: {e}")
