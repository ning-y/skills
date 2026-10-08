# PBS Pro on the Duke-NUS cluster

## Scope

Observed on `login-10-03`, 2026-10-08. PBS Pro 2022.1.6. Queue and resource
values may change; re-read them with `qstat -Q` and `qstat -Qf <queue>`.

## Commands

`qsub`, `qstat`, `qdel` live in `/cm/shared/apps/pbspro/current/bin/` and are on
the login-node `PATH`. They are **not** on `PATH` inside job scripts on compute
nodes, so do not call `qstat` from a job.

Job IDs are `<sequence>.<login-node>`, e.g. `727323.hn-10-03`.

## Queues

`workq`, `super`, `long`, `short`, `gpu`, `gpu-h200`, `gpu-h200-int`. `workq` is
the ordinary CPU queue and the safe default. `long` is for long walltimes and
large memory.

The `gpu` queue, from `qstat -Qf gpu`:

- `resources_min.ngpus = 1` - a GPU job **must** request `ngpus=1` or it is
  rejected.
- `resources_max.ngpus = 1` - one GPU per job.
- `max_queued = [u:PBS_GENERIC=1]` - at most one queued job per user; a second
  submission sits outside the queue until the first starts.
- `resources_max.mem = 250gb`, `resources_max.ncpus = 125`,
  `resources_max.walltime = 240:00:00`.

GPU nodes seen: `computeg02` and `computeg03` with 2 GPUs each, `computeg04`
with 8. On `computeg03`: 2 x `A100-PCIE-40GB` (40 GB each), driver 580.82.07,
CUDA 13.0. A CUDA 12.1 user-space runtime is comfortably within range of that
driver; older CUDA runtimes also work.

Probe GPUs from inside a job, not from the login node:

```bash
#PBS -q gpu
#PBS -l select=1:ncpus=1:mem=1gb:ngpus=1
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
```

## Job script template

```bash
#!/bin/bash
#PBS -N myjob
#PBS -q workq
#PBS -l select=1:ncpus=4:mem=16gb
#PBS -l walltime=01:00:00
#PBS -j oe
#PBS -o /absolute/path/to/logs/myjob.out

set -euo pipefail
cd /absolute/path/to/project

module load apptainer 2>/dev/null || true
APPTAINER=$(command -v apptainer || echo /apps/apptainer/1.3.2/bin/apptainer)

echo "host=$(hostname) job=${PBS_JOBID:-local}"
# ... real work ...
echo "MYJOB OK"
```

## Gotchas

- **`-o` does not create directories.** Create the log directory before the
  first `qsub`, or PBS drops the output.
- **A finished job is invisible.** Once a job leaves the queue, `qstat` no
  longer reports it, and the exit status is gone. Emit an explicit marker (for
  example `echo "MYJOB OK"`) on the last line and check for it in the log.
  A job that crashed and a job that succeeded look the same in `qstat`.
- **Job stdout carries profile noise.** `~/.bash_profile` runs a screenfetch
  banner and calls `qstat`; on a compute node that prints
  `qstat: command not found`. Skip the first lines when parsing logs.
- **Queue a job, then poll:**

  ```bash
  JID=$(qsub job.pbs)
  while [ -n "$(qstat -f "$JID" 2>/dev/null | awk '/job_state/{print $3}')" ]; do sleep 10; done
  ```

  `job_state` values include `Q` (queued), `R` (running), `E` (exiting).
- **Long jobs need a walltime that covers them.** The `walltime` in `#PBS -l`
  is a hard limit; PBS kills the job at it.
- **Cancel with `qdel <jobid>`.** If you interrupt whatever was waiting on a
  job, `qstat -u $USER` and `qdel` the orphans, or they keep running and write
  outputs later.
- **Do not run compute on the login node.** It is for building, downloading and
  editing. Submit even short CPU work; a small `workq` job is cheap.

## Submitting many small jobs

Write one `.pbs` per logical unit and keep the log directory in place. Each job
gets its own node-local `/tmp`; do not assume it is shared with another job.