# Apptainer on the Duke-NUS cluster

## Scope

Verified with Apptainer 1.3.2 (`module load apptainer`, also at
`/apps/apptainer/1.3.2/bin/apptainer`) on `login-10-03`, 2026-10-08, across two
image builds. Generic Apptainer behaviour is described as such; the
cluster-specific parts were measured here.

## fakeroot

Build with `apptainer build --fakeroot`. This works with the module default
`apptainer/1.3.2` even though the user has no `/etc/subuid` entry - Apptainer
falls back to a root-mapped namespace and ships its own fakeroot helper.

`apptainer/1.2.0` reportedly cannot: it has no fakeroot binary, so `%post`
`apt-get` fails with `setgroups 65534 failed - setgroups (1: Operation not
permitted)` and the build ends `exit status 255`. (This is carried over from the
`batregen3` host notes for the same cluster, not re-tested here.) A failed build
deletes the target `.sif`, so it must be rebuilt afterwards.

## Definition file essentials

```text
Bootstrap: docker
# docker.io/library/ubuntu:22.04, digest resolved 2026-10-08
From: ubuntu@sha256:<manifest-list-digest>

%files
    ./containers/container-lock.sh /usr/local/bin/container-lock

%post
    set -eux
    ...

%environment
    export PATH=/opt/venv/bin:$PATH

%test
    /opt/venv/bin/python /opt/selftest.py

%runscript
    exec /opt/venv/bin/python /opt/app/main.py "$@"
```

- `%files` source paths resolve against the **current working directory of
  `apptainer build`**, not the `.def` file's directory. Build from the project
  root (`cd project && apptainer build --fakeroot containers/x.sif containers/x.def`).
- `%environment` is executed as a script at container start, so `$HOME` and
  `${VAR:-default}` work there.
- `%test` runs automatically at the end of `apptainer build`; a non-zero exit
  fails the build. Run it on demand with `apptainer test <sif>`. Put a real
  model-loading check here - it is the cheapest place to catch a broken stack.
- `%runscript` backs `apptainer run <sif> <args>`.
- Use absolute interpreter paths in `%test`/`%runscript` rather than relying on
  `%environment` being applied.

## Pinning the base image

Pin by digest, and record the human-readable tag in a comment. Resolve a Docker
Hub manifest-list digest with a bearer token:

```bash
TOKEN=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:pytorch/pytorch:pull" | jq -r .token)
curl -sI -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
  "https://registry-1.docker.io/v2/pytorch/pytorch/manifests/<tag>" | grep -i docker-content-digest
```

NVIDIA NGC uses a different auth endpoint:

```bash
TOKEN=$(curl -s "https://nvcr.io/proxy_auth?scope=repository:nvidia/cuda:pull" | jq -r .token)
curl -sI -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
  "https://nvcr.io/v2/nvidia/cuda/manifests/<tag>" | grep -i docker-content-digest
```

- NGC rejects `application/vnd.oci.image.index.v1+json` with HTTP 400; ask for
  the Docker manifest-list type.
- With `Bootstrap: docker`, `From:` must be `repo@sha256:...` or `repo:tag`.
  Writing `docker://repo:tag` there fails with
  `invalid image source: invalid reference format`.

## Runtime semantics that bite

- **The SIF is read-only.** Any file the program writes at runtime must land in
  a bind-mounted directory or `$HOME`. A program that caches next to its own
  source (a common pattern) will crash on the first cache miss. Either redirect
  such paths via config, or make the cache directory configurable and point it
  at something writable.
- **Apptainer binds `$HOME`, `/tmp`, `/var/tmp` and the working directory by
  default.** Consequences: anything baked into the image under those paths is
  shadowed at runtime, so a venv or interpreter installed under `/root` or
  `$HOME` can vanish. Install interpreters and virtualenvs under `/opt`.
- Pass `--cleanenv` for reproducibility, then supply what you need with
  `--env NAME=value`. `%environment` still applies under `--cleanenv`.
- GPU: add `--nv`. Without it `torch.cuda.is_available()` is `False`.

## Build cost and failure modes

- Builds are long. Pulling a CUDA base, installing a torch/dgl stack, and
  downloading multi-GB weights took 12 min (ProteinMPNN) and 20-45 min
  (RFdiffusion, dominated by the weight download and squashfs).
- **A build is not resumable.** Killing the launching shell can take
  `apptainer build` down with it, and there is no partial result - the next
  attempt starts from zero. Run long builds detached (or as a job with no
  deadline) and confirm the process is still alive mid-flight
  (`pgrep -af "apptainer build"`).
- Build scratch goes to `/tmp` as `build-temp-*` and `bundle-temp-*`. A ~10 GB
  image needed > 20 GB there. Login-node `/tmp` had 4 TB free.
- Pulled base-image layers are cached in `~/.apptainer/cache` (grew 13 GB to
  16 GB), so a rebuild re-pulls little.
- Cleanup: killing a build leaves root-owned `build-temp-*` directories that
  the user cannot remove. Remove what you can (`rm -rf /tmp/build-temp-*`) and
  ignore the rest.

## Installing toolchains inside images

- The `uv` installer appends a line to **both** `~/.bash_profile` and
  `~/.bashrc`. In a container that touches `/root` and is harmless; on a login
  node it silently edits the user's shell profiles. Always set
  `UV_NO_MODIFY_PATH=1` before running it.
- Keep `UV_PYTHON_INSTALL_DIR` and the virtualenv under `/opt` for the `$HOME`
  binding reason above.
- `build-essential` is needed for any dependency without a `cp39`/`cp310` wheel
  (for example `subprocess32`, pulled in by `dgl`) - those compile from source.

## Verifying a built image

1. `apptainer test <sif>` - runs `%test`; this already ran during the build.
2. `apptainer exec --cleanenv <sif> <cmd>` on the login node for cheap checks.
3. Run the real workload as a PBS job, on CPU and, if relevant, on the GPU queue
   with `--nv`. This is the only check that proves the image works where it will
   actually be used.