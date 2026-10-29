# Profile MPAS: Build, Partition, and Run Scaling Studies

## Overview

This repository sets up, builds, and submits MPAS-Atmosphere experiments on Ursa, with or without Scalasca/Score-P profiling. You describe a *study* in a single YAML file: which MPAS to use, which resolution, and how many nodes and ranks. One command then builds MPAS, generates any missing grid partitions, writes a run directory for every experiment in the study, and submits them. The main use case is a strong-scaling study, where the same case runs at 1, 2, 4, ... nodes, but nothing stops you from comparing MPAS versions or profiled and unprofiled builds in the same study.

Each experiment directory records exactly what produced it (the MPAS commit, this repository's commit, the rendered job card, the `gpmetis` command), so a result can be reproduced later.

## Setup

Begin by cloning this repository to the location you would like to keep your studies, and entering it:

```bash
git clone https://github.com/AlexanderHrabski-NOAA/Profile-MPAS.git
cd Profile-MPAS
```

### Python dependencies

The driver is `build-and-run.py`. It needs Python 3.8 or newer with PyYAML and Jinja2:

```bash
pip install --user -r requirements.txt
```

If you would rather keep these out of your user site, create a virtual environment first. Either way, the driver runs on the login node and does not need the compiler modules loaded; it loads them itself when it builds.

### MPAS source

MPAS lives in the `MPAS-Model` submodule. Initialize it with:

```bash
git submodule update --init
```

The commit the submodule is pinned to is what a study builds unless you ask for something else. Note that the build step works on a clone of this directory, never on the submodule itself, so you can leave it alone. If the submodule can't be cloned, or you would like to build from a different repository, you can point a study at any MPAS clone with `mpas: {repo: /path/to/MPAS-Model}` (see below).

### METIS

MPAS decomposes the grid with a METIS partition file, and one is needed for every rank count you run. When a partition is missing, the driver generates it with `gpmetis`. Build METIS once with the Intel compilers loaded:

```bash
# with the Intel oneAPI compilers (icx) available, e.g. after `module use`/`module load` of your usual stack
./utils/build-metis.sh
```

This installs `gpmetis` into `utils/METIS/install/bin`, which the driver looks for first. A `gpmetis` already on your `PATH` also works.

### Data directories

The grids, ICs, and fix files are large and live outside the repository. Create (or symlink) three directories at the top level of the repository, each with one subdirectory per resolution:

```
ic/60km/          initial conditions and static files:
                    global.60km.init.nc, global.60km.ugwp_oro_data.nc, ...
fix/60km/         everything else MPAS reads at runtime (RRTMG data, lookup tables, ...)
partition/60km/   global.60km.graph.info, plus the generated
                    global.60km.graph.info.part.<nranks> files
```

Every file in `ic/<res>/` and `fix/<res>/` is symlinked into each experiment directory for that resolution. From `partition/<res>/`, only the partition file for that experiment's rank count is linked. The `ic`, `fix`, and `partition` directories are ignored by git, so symlinking them to the shared locations is fine.

### Resolutions

A resolution is a name that selects a grid. They are defined in `config/resolutions.yaml`:

```yaml
60km:
  stem: global.60km     # prefix of the grid files
  mesh: x1.40962        # mesh id in the surface-update file name
  dt: 300.0             # config_dt
```

To add one, add an entry here and create `ic/<name>/`, `fix/<name>/`, and `partition/<name>/`. The stem determines the file names, so the init file must be `<stem>.init.nc` and the graph file `<stem>.graph.info`.

## The Setup YAML

A study is a YAML file with a name, a block of defaults, and a list of experiments. This is `studies/strong-scaling-60km.yaml`:

```yaml
study: strong-scaling-60km

defaults:
  resolution: 60km
  tasks_per_node: 96
  profile: true
  walltime: "00:40:00"

experiments:
  - sweep:
      nodes: [1, 2, 4, 8, 16]
```

Each entry in `experiments` is laid over `defaults` to make an experiment. An entry with a `sweep` expands into one experiment per combination of the listed values (a cartesian product), so the file above makes five experiments. You can mix sweeps and single experiments freely, for example to add an unprofiled baseline:

```yaml
experiments:
  - sweep: {nodes: [1, 2, 4, 8]}
  - {name: baseline_noprof, nodes: 4, profile: false}
```

Sweep keys can reach into `mpas` with a dotted name, which is how you compare MPAS versions: `sweep: {mpas.ref: [MPAS-v8.4.2-1.19, develop], nodes: [4, 8]}`.

### Keys

| Key | Default | Meaning |
|---|---|---|
| `resolution` | (required) | A key from `config/resolutions.yaml`. |
| `nodes` | (required) | Number of nodes. |
| `tasks_per_node` | | MPI ranks per node. Total ranks = `nodes * tasks_per_node`. |
| `ntasks` | | Total MPI ranks instead. Give exactly one of `tasks_per_node` or `ntasks`. |
| `cpus_per_task` | `1` | Threads per rank. |
| `profile` | `true` | Build and run with Score-P instrumentation and `scalasca --analyze`. |
| `mpas.ref` | submodule pin | Branch, tag, or commit hash of MPAS to build. |
| `mpas.repo` | the `MPAS-Model` submodule | Path or URL of a different MPAS repository. |
| `walltime` | `00:40:00` | Slurm time limit. |
| `account`, `qos` | `fv3-cpu`, `batch` | Slurm account and QOS. |
| `start_time`, `run_duration` | `2026-1-26_00:00:00`, `7_00:00:00` | Written to `config_start_time` and `config_run_duration`. |
| `name` | generated | Experiment directory name. |

Unknown keys are an error, so a typo fails immediately instead of being silently ignored. The generated name looks like `60km_MPAS-v8.4.2-1.19_prof_n4x96`: resolution, MPAS ref, `prof` or `norm`, then nodes and ranks per node (or `n4t384` for nodes and total tasks). Names must be unique within a study.

Note that `profile` is a property of the experiment, not of MPAS. It changes how MPAS is built (the compiler wrappers in the modulefile) and how it is launched, but not which source is used.

## Running a Study

Always start with a dry run. It validates the YAML, resolves every MPAS ref to a commit, renders every template, and prints what would happen, without cloning, building, or submitting anything:

```bash
./build-and-run.py --dry-run studies/strong-scaling-60km.yaml
```

```
Experiments:
  name                               res   nodes  ranks  tpn  mpas          profile
  60km_MPAS-v8.4.2-1.19_prof_n1x96   60km  1      96     96   a064c194c1a0  prof
  ...

Builds: 1
  a064c194c1a0-prof  [build]

Partitions:
  60km x 96: generate
  ...
```

The tool prints one row per experiment, how many builds it needs, and which partitions will be generated. Once that looks right, run it for real:

```bash
./build-and-run.py studies/strong-scaling-60km.yaml
```

The steps, in order:

1. **Validate.** Check the YAML, the resolutions, the data directories, and resolve each `mpas.ref` to a full commit hash. If a ref isn't in the source repository yet, the driver runs `git fetch` there and tries again.
2. **Build.** MPAS is cloned from the source repository and checked out at the resolved commit, then built with `build.sh`. There is one build per unique *(commit, profile)* pair, shared by every experiment that needs it, so a five-point scaling study builds once, and the same binary runs at every scale. A finished build is marked with a `.build-ok` file and reused on later runs.
3. **Partition.** For each rank count that doesn't have a `partition/<res>/<stem>.graph.info.part.<nranks>` file, the driver runs `gpmetis -minconn -contig -niter=200 <stem>.graph.info <nranks>`. A single-rank run needs no partition file and MPAS doesn't read one.
4. **Stage.** Each experiment directory is written and then submitted with `sbatch`.

Everything that can fail (validation, builds, partitions) happens before the first job is submitted, so a failed build leaves nothing half-submitted.

### Options

| Flag | Effect |
|---|---|
| `--dry-run` | Validate and print the plan. Changes nothing. |
| `--no-submit` | Build and stage, but don't call `sbatch`. Useful for looking at a job card before it goes in the queue. |
| `--rebuild` | Rebuild MPAS even when a finished build exists. |
| `-o`, `--overwrite` | Replace experiment directories that already exist. Without it the driver refuses to touch them. Builds are kept either way. |

## What You Get

Everything is created under `runs/<study>/`, which is ignored by git:

```
runs/strong-scaling-60km/
  study.yaml                    copy of the setup file used
  builds/a064c194c1a0-prof/     clone of MPAS at that commit, with build/ inside
  experiments/60km_MPAS-v8.4.2-1.19_prof_n4x96/
    job.fcst                    the batch script that was submitted
    namelist.atmosphere         rendered for this experiment
    streams.atmosphere
    stream_list.*               copied from config/mpas
    scorep.filt                 copied from config/scalasca (profiled runs only)
    modulefiles/ursa.intel.lua
    module-setup.sh
    global.60km.init.nc  ...    symlinks to ic/ and fix/
    global.60km.graph.info.part.384   symlink to the partition
    provenance.yaml
    jobid
```

The job runs the binary inside the study's own `builds/` directory, not a shared build somewhere else, so rerunning an old experiment uses the executable it was built with.

### Reproducing an experiment

`provenance.yaml` records the resolved experiment settings, the MPAS commit and build location, the commit of this repository (and whether it had uncommitted changes), a hash of the modulefile, the `gpmetis` command if one was run, and the job ID. The clone in `builds/` keeps its `origin` pointing at the repository it was cloned from, so `git -C builds/<build> remote -v` and `git -C builds/<build> log -1` tell you where the source came from.

One thing to know: MPAS's `build.sh` reads the modulefile from inside its own tree, so the driver writes the rendered `modulefiles/mpas/ursa.intel.lua` into the clone before building. The clone's `git status` will therefore show that one file as changed (or added). This is expected.

## Looking at Profiles

A profiled job writes a Score-P measurement directory (its name starts with `scorep_`) in the experiment directory. `sourceme.sh` loads the tools used to examine it (`cube`, `cubelib`, and Qt for the GUI). Source it, and then use Scalasca as usual:

```bash
source sourceme.sh
scalasca --examine <the scorep_ directory>
```

The `sourceme.sh` module path is specific to one user's scratch directory, so you will likely need to edit it for yours. Edit `config/scalasca/scorep.filt` to change what Score-P filters out of the measurement; it is copied into every profiled experiment when it is staged.

## Customizing

The files in `templates/` are [Jinja2](https://jinja.palletsprojects.com/) templates, and they are the single source of truth for what gets written to an experiment:

- `job.fcst.j2`: the Slurm script. The profiled and unprofiled versions are the same template; the Score-P environment and the `scalasca --analyze` launcher appear only when `profile` is true.
- `ursa.intel.lua.j2`: the modulefile used for both the build and the run. The profiled version differs only in the `scorep-` prefix on the compiler wrappers.
- `namelist.atmosphere.j2` and `streams.atmosphere.j2`: the MPAS namelist and streams. Only the values that depend on the resolution, the start time and duration, and the partition prefix are variables; everything else is as you would write it by hand.

Missing variables are an error rather than an empty string, so if you add `{{ something }}` to a template and forget to pass it, the dry run will tell you.

To add a new setup key, add it to `DEFAULTS` or `EXPERIMENT_KEYS` in `mpas_profile/config.py`, pass it through the matching function in `mpas_profile/render.py`, and use it in a template.

## Tests

```bash
python -m pytest tests
```

The unit tests cover the setup parsing and the templates. The end-to-end test runs a copy of the whole tool against a small fake MPAS repository, with stand-ins for `module`, `sbatch`, and `gpmetis`, so it needs no cluster. It checks that a sweep makes one build, the right partitions, and one submission per experiment, and that a failed build submits nothing. It does not replace running a real job on Ursa.
