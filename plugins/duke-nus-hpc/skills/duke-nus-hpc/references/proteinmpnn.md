# ProteinMPNN

**Status: verified working.** Built on `login-10-03`, 2026-10-08, and run as PBS
jobs on CPU (`compute1002`, `workq`) and GPU (`computeg03`, `gpu` queue, A100,
`--nv`).

## Pins

| | |
| --- | --- |
| Upstream | `https://github.com/dauparas/ProteinMPNN` @ `8907e6671bfbfc92303b5f79c4b5e6ce47cdef57` (2023-06-27) |
| Base image | `pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime` @ `sha256:ac7c098a81512e719afa5d2d497f812d7db3498f340a4b819c69cb7b3b257126` |
| Interpreter | Python 3.11.9, torch 2.4.1+cu121, numpy 2.1.1 |

ProteinMPNN needs only **torch and numpy**; its helper scripts are stdlib-only.
No extra pins are required, and the model weights ship inside the repository
(`vanilla_model_weights/`, `soluble_model_weights/`, `ca_model_weights/`, each
with `v_48_002/010/020/030`), so pinning the commit pins the weights too.

Install to `/opt/proteinmpnn` by `git init` + `git fetch --depth 1 origin <sha>`
+ `git checkout FETCH_HEAD`, then delete `.git`. Strip `.git` deliberately:
output headers then read `git_hash=unknown`, which is expected, not an error.

The image adds `/opt/proteinmpnn` and `/opt/proteinmpnn/helper_scripts` to
`PATH`, sets `PROTEINMPNN_ROOT`, and makes `apptainer run` invoke
`protein_mpnn_run.py`. `%test` loads one checkpoint from each of the three
families and warms a strict `load_state_dict` against the weights.

Build took about 12 minutes.

## Running

Unconditional / multi-PDB design (`--nv` only on a GPU job):

```bash
apptainer run --nv --bind "$PWD/work:/work" containers/proteinmpnn.sif \
    --jsonl_path /work/parsed_pdbs.jsonl --out_folder /work \
    --num_seq_per_target 2 --sampling_temp "0.1" --seed 37 --batch_size 1
```

Parse PDBs into the jsonl first, via the bundled helper:

```bash
apptainer exec --cleanenv --bind "$PWD/work:/work" containers/proteinmpnn.sif \
    python /opt/proteinmpnn/helper_scripts/parse_multiple_chains.py \
    --input_path=/work/pdbs --output_path=/work/parsed_pdbs.jsonl
```

A single PDB, directly:

```bash
apptainer exec --cleanenv --bind "$PWD/work:/work" containers/proteinmpnn.sif \
    python /opt/proteinmpnn/protein_mpnn_run.py \
    --pdb_path /work/complex.pdb --pdb_path_chains E \
    --out_folder /work --num_seq_per_target 10 --sampling_temp "0.1" --seed 37
```

Always bind a writable output directory. `--out_folder` must be writable at
runtime, and the image's own filesystem is read-only.

## Choosing chains to design

- **Single PDB:** `--pdb_path` plus `--pdb_path_chains E` designs chain E and
  holds every other chain fixed. Omit `--pdb_path_chains` to design all chains.
  Confirmed in the output header:
  `fixed_chains=['A'], designed_chains=['E']`.
- **Multiple PDBs:** `--jsonl_path` plus `--chain_id_jsonl` from
  `helper_scripts/assign_fixed_chains.py`.

## Fixed residue positions: the main trap

`--fixed_positions_jsonl` addresses positions by **1-based index into the parsed
chain sequence, not by PDB residue number**. In `tied_featurize`:

```python
fixed_position_mask[np.array(fixed_pos_list) - 1] = 0.0
```

and the mask is applied as `chain_mask = chain_mask * chain_M_pos * mask`.

So for a chain whose PDB numbering starts at 7, residue **20 is index 14**.
Getting this wrong silently fixes the wrong residues; there is no warning and no
error. Compute the index as `residue_number - first_residue_number + 1`, and
confirm the mapping before trusting a run:

```bash
apptainer exec ... python -c "
import sys; sys.path.insert(0,'/opt/proteinmpnn')
from protein_mpnn_utils import parse_PDB
d = parse_PDB('/work/complex.pdb')[0]
print(repr(d['name']), len(d['seq_chain_A']), d['seq_chain_A'])
"
```

The file format is one JSON object per line, keyed by the PDB's `name` (its
basename without `.pdb` - note that spaces and parentheses are kept):

```json
{"complex (1)": {"A": [14, 15, 16, 17, 18, 19]}}
```

Chains you do not list should map to `[]`. `helper_scripts/make_fixed_positions_dict.py`
generates this from a parsed jsonl; its `--position_list` is **also** 1-based
sequence indices, so it carries the same off-by-residue hazard.

## Reading the output

`<out_folder>/seqs/<name>.fa` holds the native sequence first, then one record
per sample:

```text
>complex, score=..., global_score=..., fixed_chains=['A'], designed_chains=['E'], model_name=v_48_020, git_hash=unknown, seed=37
SDEDLLHI
>T=0.1, sample=1, score=..., global_score=..., seq_recovery=0.5000
DDEPLLVV
```

- `score` averages over the **designed** positions; `global_score` over every
  residue in every chain. Lower is more confident.
- The printed sequence covers the whole of the **designed chains only**
  (`_S_to_seq` keeps positions where the mask is non-zero, and the printing mask
  is `chain_M`, the designed *chains*). Positions held fixed via
  `--fixed_positions_jsonl` are still present in the string - they are excluded
  from sampling through `chain_M_pos`, not from the printed sequence - and will
  therefore equal the native residue in every sample. Do not expect dashes or a
  shortened string, and do not infer the fixed set from the length.

## Caveats

- **CPU and GPU give different sequences for the same `--seed`.** Multinomial
  sampling is device-dependent. This is expected; it is not a broken seed.
- torch 2.4's `torch.load` defaults to `weights_only=False` and warns that a
  future release flips it. ProteinMPNN does not pass the flag, so a base-image
  bump to torch >= 2.6 may break checkpoint loading. The image's `%test` loads
  all three checkpoint families with a strict `load_state_dict`, so a rebuild
  catches this at build time.

## Provenance

Built from `containers/proteinmpnn.def` in
`/data/wanglf/home/e0175719/Downloads/azoitei/`, alongside `proteinmpnn.sif`
(3.1 GB), a `proteinmpnn.lock.tsv` inventory baseline, `proteinmpnn-selftest.py`,
a `Makefile` (`make`, `make check-locks`, `make accept-locks`) and PBS smoke tests
under `tests/`.