---
name: duke-nus-hpc
description: Operate on the Duke-NUS HPC cluster - login-* nodes running PBS Pro with an Apptainer module. Covers submitting and monitoring PBS jobs, building and running Apptainer images, GPU and CUDA workloads, and per-program notes for ProteinMPNN (verified) and RFdiffusion (untested). Use when the host is login-* or hn-*, when qsub/qstat/qdel are involved, or when building or running .sif containers there.
---

# Duke-NUS HPC

Operating knowledge for this cluster. Verified 2026-10-08 on the login node
`login-10-03` and, via a `workq` job, the compute node `compute1001`.

## Confirm the host

`hostname` is the primary signal but not sufficient on its own: it is bare on the
login node and qualified on compute nodes, and the same login node appears as
`hn-*` in PBS job IDs. Confirm it with a marker that is orthogonal to the name
and independent of shared storage - these live on the *node's own* root
filesystem and are identical on login and compute nodes:

    grep -q 'Cluster Manager' /etc/cm-release             # "Cluster Manager v10.0"
    ls -d /cm/conf /cm/local /cm/shared
    /cm/shared/apps/pbspro/current/bin/qstat --version    # pbs_version = 2022.1.6...

Observed values: `hostname` is `login-10-03` (and `hostname -f` agrees); on a
compute node it is `compute1001.cm.cluster`. Inside a batch job, `$PBS_JOBID` and
`$PBS_O_HOST` are set and conclusive - `PBS_O_HOST` names the submitting login
node, e.g. `login-10-03.cm.cluster`.

Do **not** use these: `~/.cm` and `~/.cmhasessions` live in the shared home on
`/data`, so they identify the account rather than the host; `/etc/pbs.conf`,
`/var/spool/pbs` and `/opt/pbs` do not exist here; the second line of
`/etc/cm-release` reads `slave` on the login node too, so it says nothing about
role. The NFS exports (`/data`, `/apps`, `/home`, `/cm/shared`, all from
`nfs1.hpc.duke-nus.edu.sg`) confirm the storage fabric only - a non-HPC host
mounting the same exports would match.

## Route the task

- Queues, resource requests, job scripts, monitoring → [references/pbs-pro.md](references/pbs-pro.md)
- Building or running `.sif` images, base-image pinning, fakeroot → [references/apptainer.md](references/apptainer.md)
- ProteinMPNN (container built and run on CPU and GPU) → [references/proteinmpnn.md](references/proteinmpnn.md)
- RFdiffusion (**untested** - the definition was written but never built) → [references/rfdiffusion.md](references/rfdiffusion.md)

## Environment

- Login node: RHEL 9.x, AMD EPYC, PBS Pro 2022.1.6. `qsub`, `qstat`, `qdel` are
  at `/cm/shared/apps/pbspro/current/bin/` and on the login-node `PATH`.
- Only `/data` is shared (`nfs1.hpc.duke-nus.edu.sg:/ifs/prod/onefs`, multi-PB).
  Home, repos and containers all live there. Node-local `/tmp` is per-node.
- Lmod: `module load apptainer` selects 1.3.2. Cluster software lives in `/apps`,
  including `/apps/apptainer/1.3.2/bin/apptainer` (already on `PATH`).
- Python tooling comes from mamba, not `nix`/`uv`:
  `eval "$(/data/wanglf/home/e0175719/miniforge3/bin/mamba shell hook --shell bash)"`,
  then `mamba activate <env>`. Envs are under `~/.local/share/mamba/envs/` and
  `~/miniforge3/envs/`. Do not assume an env has what you need: base `python3`
  has no numpy, and the `snakemake` env has numpy but no scipy.
- `nvidia-smi` exists on the login node but cannot reach a driver; probe GPUs
  from a job instead.
- The user's `~/.bash_profile` prints a screenfetch banner and calls `qstat`.
  Compute nodes lack that on `PATH`, so job stdout starts with the banner plus
  `qstat: command not found`. Harmless - ignore it.

## Core rules

1. Build, download and edit on the login node; run anything expensive through
   PBS. Login-node `/tmp` is local disk with ample room for scratch.
2. **Compute nodes have no outbound network.** Every input a job needs - model
   weights, data, the container itself - must already be on `/data` or baked
   into the image. This is the single most common way a job fails.
3. Write job scripts with `set -euo pipefail` and an explicit success marker.
   PBS job outcome is not recoverable from `qstat` once the job leaves the
   queue, so print what you need into the job's own log.
4. A container image must be self-contained: pin the base image by digest, pin
   upstream source by commit, bake weights in, verify hashes, and gate the build
   with a `%test` that loads the model. A `.sif` that depends on a runtime
   download will not work on a compute node.
5. Re-verify anything here that is version- or date-sensitive. The program notes
   in `references/` record one specific commit and build; a version bump can
   invalidate them.
