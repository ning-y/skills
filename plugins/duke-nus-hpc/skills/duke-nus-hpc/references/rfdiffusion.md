# RFdiffusion

> **Status: UNTESTED. Nothing on this page has been run end to end.**
> The definition file was written and reviewed on `login-10-03`, 2026-10-08, but
> **no image was ever produced** - both build attempts were interrupted, the
> first by a job deadline and the second on request. `%post`, `%test` and a real
> inference have never executed. Treat every claim below as a design decision
> with its reasoning, not as verified behaviour, and expect to iterate.

## Pins

| | |
| --- | --- |
| Upstream | `https://github.com/RosettaCommons/RFdiffusion` @ `86507b6538f51fce57b5a72477165f03999ed7ae` (2026-07-15) |
| Base image | `nvcr.io/nvidia/cuda:11.6.2-cudnn8-runtime-ubuntu20.04` @ `sha256:ea2af6ed2bc770f83ed30235f7369b8c83a49b58cdb0bfd55f1227d89edfad8f` |
| Interpreter | Python 3.9, via `uv` |
| Size | roughly 9-10 GB; expect a 20-45 min build |

The base image is the one the repository's own `docker/Dockerfile` and
`.github/workflows/main.yml` use, which is why the dependency set below is taken
from that CI workflow rather than invented.

## Dependencies

Pinned exactly as the repo CI installs them:

```text
numpy<2
dgl==1.0.2+cu116          -f https://data.dgl.ai/wheels/cu116/repo.html
torch==1.12.1+cu116       --extra-index-url https://download.pytorch.org/whl/cu116
e3nn==0.3.3
wandb==0.12.0
pynvml==11.0.0
decorator==5.1.0
hydra-core==1.3.2
pyrsistent==0.19.3
dllogger @ git+https://github.com/NVIDIA/dllogger@0478734ff7be75adde8d160e04872664d1c62e5f
```

then `pip install env/SE3Transformer` and `pip install -e . --no-deps`.
Resolution brought in numpy 1.26.4, scipy 1.13.1, omegaconf 2.3.1 and
subprocess32 3.5.4.

Notes:

- `dllogger` is a git dependency with no release; pin it to a commit
  (`0478734f...` was `master` HEAD on 2026-10-08), not to the branch.
- `numpy<2` is required by this torch/dgl combination.
- **`build-essential` is required.** `subprocess32` has no `cp39` wheel and
  compiles from source.
- Python 3.9 comes from `uv` (`uv python install 3.9`, `uv venv /opt/venv`),
  matching the CI. Keep `UV_PYTHON_INSTALL_DIR` and the venv under `/opt`, and
  set `UV_NO_MODIFY_PATH=1` so the installer does not edit shell profiles.
- `DGLBACKEND=pytorch` must be set in the container environment; DGL otherwise
  looks for `~/.dgl/config.conf` and could pick up the wrong backend from the
  user's `$HOME` at runtime.

## Model weights

Nine checkpoints, about **4.1 GB**, fetched from
`https://files.ipd.uw.edu/pub/RFdiffusion/<hash>/<name>.pt`. Compute nodes have
no network, so all of them must be baked into the image.

- `Base_ckpt.pt` - default
- `Complex_base_ckpt.pt` - used when `ppi.hotspot_res` is set
- `Complex_Fold_base_ckpt.pt` - used when `scaffoldguided.scaffoldguided` is true
- `InpaintSeq_ckpt.pt`, `InpaintSeq_Fold_ckpt.pt` - used for inpaint-seq/str
- `ActiveSite_ckpt.pt`, `Base_epoch8_ckpt.pt`, `Complex_beta_ckpt.pt`,
  `RF_structure_prediction_weights.pt` - optional, referenced by the bundled
  examples via `inference.ckpt_override_path`

Selection among the first five is automatic (`rfdiffusion/inference/model_runners.py`),
which is why shipping only `Base_ckpt.pt` would break most documented use.

**The `<hash>` path segment is not a content hash.** Its MD5 matches nothing in
the downloaded file, so it cannot be used as a checksum. Pin sha256 separately
and verify with `sha256sum -c` in `%post`; a manifest of all nine is recorded
next to the definition.

## Runtime write hazard: the IGSO3 schedule cache

`Diffuser` caches its numerical IGSO3 tables as a pickle under
`<repo>/schedules/`, i.e. `/opt/RFdiffusion/schedules` in the image, because
`inference.schedule_directory_path` defaults to `null`. Two consequences inside
a read-only SIF:

1. `os.mkdir` of that directory, or `write_pkl` into it, fails.
2. Changing `diffuser.T` (the README explicitly encourages tuning it) changes
   the cache filename, so a baked cache does not help and the write is retried.

The definition therefore redirects that one key in `config/inference/base.yaml`:

```yaml
schedule_directory_path: ${oc.env:RFDIFFUSION_SCHEDULES,${oc.env:HOME}/.rfdiffusion-schedules}
```

Nested `oc.env` resolvers resolve correctly under omegaconf 2.3.1, and `$HOME`
persists across jobs on this cluster, so the one-off IGSO3 computation is paid
once per user rather than once per job. **Patch only `base.yaml`:**
`config/inference/symmetry.yaml` has no such key and inherits it via
`defaults: - base`. (A `grep` for the patched text in `symmetry.yaml` will fail
and, under `set -e`, abort the build.)

`inference.model_directory_path` needs no equivalent treatment: when `null` the
code resolves it to `<repo>/models`, which is exactly where the weights are
baked.

## Checkpoint config is a superset of the constructor

`ckpt['config_dict']['model']` contains keys the model does not accept, notably
`d_time_emb` and `d_time_emb_proj`. Passing that section verbatim -
`RoseTTAFoldModule(**cfg['model'], ...)` - raises `TypeError`. The code avoids
this in `Sampler.assemble_config_from_chk`, which iterates the *inference
config's* keys and only overwrites their values from the checkpoint. Any
self-test or script must mirror that, not splat the checkpoint section.

## To finish

One build, from the project root with `module load apptainer` (1.3.2,
`--fakeroot`). Run it detached: builds are long and are not resumable, and
killing the launching shell can take the build with it.

```bash
cd /data/wanglf/home/e0175719/Downloads/azoitei && module load apptainer && make
```

Then verify as for any image: `apptainer test`, then a real inference as a PBS
job on `workq` and again on the `gpu` queue with `--nv`. A minimal run is an
unconditional monomer, e.g. `'contigmap.contigs=[60-60]' inference.num_designs=1
inference.deterministic=True diffuser.T=20`, then check that a 60-residue
backbone `.pdb` and a `.trb` sidecar were produced.

## Unverified risks

Listing these explicitly because none has been exercised:

- **torch 1.12.1+cu116 on driver 580.82.07 / CUDA 13.0.** CUDA is backward
  compatible so this is expected to work, but it was never observed.
- **The schedule-directory redirect.** Read from the source, never executed.
- **The strict model load in `%test`.** It mirrors `assemble_config_from_chk` +
  `load_model`, but has not been run.
- **`dgl 1.0.2+cu116`** on these nodes, and the `se3-transformer` install.
- **Overall build success.** Nothing has reached the end of `%post`.

## Provenance

Written alongside `containers/rfdiffusion.def`, `rfdiffusion-models.sha256` and
`rfdiffusion-selftest.py` in
`/data/wanglf/home/e0175719/Downloads/azoitei/`. The `Makefile` there builds
`containers/*.def` generically.