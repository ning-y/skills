# Dropbox access with rclone (read-only)

EVOS M7000 scans are normally written to the lab Dropbox. This environment has
`rclone` configured with a Dropbox remote; `rclone listremotes` currently shows
`nus:`. Confirm the remote name in the current environment; do not assume it.

## Hard rule: Dropbox is read-only

Never upload, create, rename, move, copy to, delete, or change sharing on
anything under a Dropbox remote. The configured credential may have write
permission; do not rely on the server to refuse a mistake.

Treat the remote as production data shared with other lab members.

Allowed against a Dropbox remote:

- listing and inspection: `ls`, `lsd`, `lsl`, `lsf`, `lsjson`, `size`, `about`;
- reading a file: `cat`;
- downloading to local storage: `copy` or `copyto` with the remote as the
  **source** and a local path as the destination;
- integrity checks: `check` or `hashsum` with the remote as the source.

Forbidden:

- `copy`, `copyto`, `sync`, or `move` with a Dropbox path as the destination;
- `sync` in either direction (a swapped argument can delete or overwrite remote
  data);
- `delete`, `purge`, `mkdir`, `rmdir`, `touch`, `dedupe`;
- `link` (it creates or changes a public share link);
- `mount` or `serve` without `--read-only`;
- `config` subcommands, and `config show` / `config dump` in particular, which
  print credentials and tokens.

Do not print, copy, or commit `rclone.conf`, tokens, or Dropbox URLs containing
`rlkey=` / `st=` parameters. Those URLs grant access.

## Locate the remote and folder

```bash
rclone listremotes
rclone lsd nus: --max-depth 1
rclone lsf "nus:<folder>" --max-depth 1 --dirs-only
```

Dropbox paths are case-insensitive but preserve their stored case. Quote paths
that contain spaces or commas; use single quotes when the path contains an
apostrophe.

URL mapping:

- `https://www.dropbox.com/home/<path>` corresponds to `nus:<path>` after
  URL-decoding.
- `https://www.dropbox.com/scl/fo/<id>/...` and `/scl/fi/...` are share links,
  not rclone paths. Find the folder by listing the remote; shared folders
  normally appear at the remote root. If the folder cannot be identified, ask
  the user for its path. Do not call `rclone link` to resolve it.

## Download an EVOS scan

Honor an explicit local destination. Otherwise choose a task-named directory
under the current working directory, not an arbitrary folder in the home root.
Announce the exact absolute destination before doing work. Keep downloaded
archives/raw inputs, managed scratch, final outputs, and small evidence separate:

```bash
task_root="$(pwd -P)/evos-scan-review"  # replace with explicit destination if given
printf 'Local destination: %s\n' "$task_root"
mkdir -p "$task_root"/{inputs,scratch,outputs,evidence}
remote='nus:<folder>/scan.2026-09-29-09-57-04'
local="$task_root/inputs/scan.2026-09-29-09-57-04"

rclone lsjson "$remote" --max-depth 1
rclone copy "$remote" "$local" \
  --include '*.[sS][cC][aA][nN][pP][rR][oO][tT][oO][cC][oO][lL]' \
  --include 'scan_*_R_*.[tT][iI][fF]' \
  --include 'scan_*_R_*.[tT][iI][fF][fF]' \
  --immutable --checksum \
  --transfers 4 --checkers 8 --progress
```

Download raw tiles and scan protocols; `_M_` previews and `.stitch` files are
not required for stitching. Preserve acquisition directory boundaries.

- `--dry-run` first if the path or filter is new.
- `--immutable` refuses to overwrite existing local files.
- `--checksum` compares content hashes when the backend provides them.
- Add `--include 'scan_*_M_*.TIF'` only when the merged RGB previews are needed.
- Dropbox may print `Committing uploads` while tearing down a read-only or
  dry-run operation. Do not infer a write from that line; confirm by listing
  the remote.

Verify the download:

```bash
rclone check "$remote" "$local" --one-way --download \
  --include '*.[sS][cC][aA][nN][pP][rR][oO][tT][oO][cC][oO][lL]' \
  --include 'scan_*_R_*.[tT][iI][fF]' \
  --include 'scan_*_R_*.[tT][iI][fF][fF]'
```

Then review the local stitch plan:

```bash
python3 scripts/stitch_evos_m7000.py "$local" --dry-run
```

`rclone check --download` verifies downloaded content against the remote by
reading content, rather than relying only on names/sizes or available backend
hashes. Record the command, filters, result, and scope; it does not prove that
the acquisition itself is scientifically complete.

Never set `--output-dir` or `--workdir` to a Dropbox path. Use sibling local
`outputs` and `scratch` directories, never the raw input tree. Make raw files
read-only, not directories recursively unwritable. Keep sources unchanged;
after validation, remove only explicitly task-managed scratch while retaining
small configurations, logs, and verification evidence. Never automatically
delete source archives, source tiles, or user-owned work.

## ZIP input: inventory before selective extraction

Download the archive with remote-source `rclone copyto --immutable`, then use
`rclone check --download` on its parent directories with a filter selecting that
archive. Do not extract directly from a remote or into the archive's directory.
Keep the downloaded ZIP in `inputs`; extract into a **new** child of `inputs`,
with `scratch` and `outputs` outside that extraction root.

The standard-library recipe below inventories every member, validates all paths
and destination collisions before extraction, and streams only raw
`scan_*_R_*.TIF`/`.TIFF` and `.scanprotocol` files while preserving acquisition
directories. Save it as a local task script (not inside the extraction root):

```python
import json
import re
import shutil
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

archive = Path(sys.argv[1]).resolve(strict=True)
root = Path(sys.argv[2]).resolve()
if root.exists() or archive.is_relative_to(root):
    raise ValueError("Extraction root must be new and separate from the archive")

with zipfile.ZipFile(archive) as z:
    entries = []
    destinations = {}
    for info in z.infolist():
        name = info.orig_filename
        path = PurePosixPath(name)
        parts = name.rstrip("/").split("/")
        mode = info.external_attr >> 16
        if (not name or "\\" in name or "\x00" in name
                or path.is_absolute() or PureWindowsPath(name).drive
                or any(p in ("", ".", "..") or ":" in p for p in parts)
                or stat.S_ISLNK(mode)
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise ValueError(f"Unsafe ZIP member: {name!r}")
        key = "/".join(parts).casefold()
        if key in destinations:
            raise ValueError(f"Duplicate destination: {name!r}")
        destinations[key] = info.is_dir()
        selected = not info.is_dir() and (
            re.fullmatch(r"scan_.*_R_.*\.tiff?", path.name, re.IGNORECASE)
            or path.suffix.lower() == ".scanprotocol")
        entries.append((info, parts, bool(selected)))
        print(json.dumps({"name": name, "bytes": info.file_size,
                          "compressed_bytes": info.compress_size,
                          "selected": bool(selected)}), flush=True)
    for key in destinations:
        parents = PurePosixPath(key).parents
        if any(str(p) in destinations and not destinations[str(p)]
               for p in parents):
            raise ValueError(f"File/directory collision: {key!r}")
    print(json.dumps({"members": len(entries),
                      "bytes": sum(i.file_size for i, _, _ in entries),
                      "compressed_bytes": sum(i.compress_size for i, _, _ in entries),
                      "selected_members": sum(s for _, _, s in entries),
                      "selected_bytes": sum(i.file_size for i, _, s in entries if s)}),
          flush=True)
    if not any(s for _, _, s in entries):
        raise ValueError("No raw tiles or scan protocols selected")
    # Inventory-only mode lets the operator review sizes and free space first.
    if "--extract" in sys.argv[3:]:
        root.mkdir(parents=True, exist_ok=False)
        for info, parts, selected in entries:
            if not selected:
                continue
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            target.chmod(target.stat().st_mode & ~0o222)
```

```bash
python3 "$task_root/extract_raw.py" "$task_root/inputs/scan.zip" \
  "$task_root/inputs/extracted" > "$task_root/evidence/zip-inventory.jsonl"
# Review inventory, selected counts/sizes and available disk space before extraction.
python3 "$task_root/extract_raw.py" "$task_root/inputs/scan.zip" \
  "$task_root/inputs/extracted" --extract \
  > "$task_root/evidence/zip-extraction.jsonl"
```

Use a private, task-owned local tree; do not allow concurrent writers to its
extraction paths. Reading each selected entry to EOF checks its ZIP CRC; any
exception leaves an incomplete extraction that must not be used for stitching.
Retain the error evidence and use a fresh extraction root for a corrected run.
Unselected payloads are not decoded or CRC-checked. Successful ZIP parsing,
inventory, or selective extraction does **not** prove the whole archive valid;
an `unzip` failure alone does not prove corruption (unsupported ZIP features,
tool limits, permissions, or storage failures can also fail extraction).
Never rerun a user-reported failing command just to confirm it.

## Listing-only audit

Record a remote listing before and after the task to detect listed-name changes:

```bash
rclone lsf "$remote" --recursive --files-only | sort > "$task_root/evidence/dropbox-before.txt"
# ... listings and downloads only ...
rclone lsf "$remote" --recursive --files-only | sort > "$task_root/evidence/dropbox-after.txt"
diff "$task_root/evidence/dropbox-before.txt" "$task_root/evidence/dropbox-after.txt"
```

An empty `diff` means only that the listed names agree; it does not establish
unchanged remote contents or remote integrity. Use the download content check
above for that scope. Listing large folders recursively can be slow; use the
project folder, not the whole team Dropbox. Report checks performed separately
from conclusions, and do not claim this audit proves no concurrent changes.

## Dropbox-specific notes

- These are team folders; other users may change them concurrently. A download
  is a snapshot, not a lock.
- Large scans contain hundreds to thousands of small TIFFs. Keep transfers
  modest (`--transfers 4 --checkers 8`) to avoid throttling.
- Do not use Google Drive flags such as `--drive-chunk-size`; Dropbox ignores
  or rejects them.
- `rclone size` is recursive and read-only. It can take time on a full project
  folder; prefer `lsjson --max-depth 1` when only the immediate contents
  matter.
