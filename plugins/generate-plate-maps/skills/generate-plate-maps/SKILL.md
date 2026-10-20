---
name: generate-plate-maps
description: Generate deterministic experimental plate maps in Python from validated structured data, including mixed common plate formats, categorical views, faceted layouts, legends, empty wells, and PNG/SVG/PDF exports. Use for assay layouts, ICC staining maps, donor/treatment maps, or reusable computational plate-map workflows. Never use generative image synthesis for these maps.
---

# Generate Plate Maps

Build plate maps from explicit experimental assignments. Keep the data
specification, well allocation, validation and Matplotlib rendering separate.
Accept conversational descriptions; do not require the user to edit JSON or CSV.

## Prerequisites

- Python 3.10+ with Matplotlib in the active environment. The helper uses the
  standard library plus Matplotlib; no image-generation service is required.
- The configured font must be installed. Default to Matplotlib's DejaVu Sans.
- Use Poppler's `pdftoppm` or another PDF rasterizer for visual verification.

Verified with Python 3.12.14 and Matplotlib 3.10.8. Record the actual versions in
exports; do not promise identical typography across arbitrary environments.

## Route the task

- Read [references/schema.md](references/schema.md) for the data model, styles,
  named formats and internal placement policy.
- Use `scripts/plate_maps.py` as the reusable renderer and validator.
- Use `scripts/allocation.py` to assign positions before creating well records.
- Use `assets/two_plate_icc.json` or `assets/four_plate_icc_sudan_black.json` as
  structural examples. Do not copy their biological assignments into a new
  experiment unless the user requests that experiment.

## Core workflow

1. Extract plates, donors/samples, treatments, stains, control types, doses and
   replicate identities from the conversation. Keep these fields separate.
   Resolve contradictory biological assignments before drawing; do not infer
   treatment from the plate name or display label.
2. Honor requested row/column grouping and fixed coordinates. For new or
   explicitly rearranged layouts, prefer interior wells using
   `allocate_grouped_positions`. Use peripheral positions only when interior
   capacity is insufficient. Keep this planning rationale and allocation
   diagnostics out of the figure and PDF.
3. Create a JSON specification with one explicit record for every physical well,
   including empties. Use ordered category/color definitions and string-valued
   categorical attributes. Keep all empty wells visible by default.
4. Define views by selecting attributes from the same physical records. Place
   views in facet rows and plates in facet columns. Never duplicate or mutate
   physical assignments to create another view.
5. Include supplied doses with units in treated-well labels. Retain exposure,
   fixation timing and reference markers as separate attributes; place full
   schedules in the multiline experiment footer. Do not invent timings or
   bibliographic sources. Run the bundled renderer with an explicit output prefix. Reject missing or
   duplicate wells, invalid coordinates, unknown categories and text overflow.
   Increase the canvas or shorten explicit labels with the user's agreement;
   never silently abbreviate scientific identities or clip text.
6. Compare the exported CSV and manifest against the experiment: occupancy,
   donor order, treatments, controls, stains and replicate counts. Apply the
   explicitly specified treatment to secondary-only controls; their assay
   identity does not imply they are untreated.
7. Inspect the PNG and a rasterized PDF, especially dense control labels, row and
   column headings, legends, title spacing, empty wells and contrast. Deliver
   PNG, SVG and PDF plus the reusable JSON and long-format CSV. Include the
   manifest when an audit or reproducible rerun is useful.
8. After the first working map, ask targeted questions about meaningful layout
   refinements. Update the same specification and engine rather than starting
   a separate one-off plotting script.

## How to run

Resolve `SKILL_DIR` to this skill's installed directory. From the repository root:

```bash
SKILL_DIR="$PWD/plugins/generate-plate-maps/skills/generate-plate-maps"
python3 "$SKILL_DIR/scripts/plate_maps.py" \
  "$SKILL_DIR/assets/four_plate_icc_sudan_black.json" \
  --output-prefix /absolute/path/output/plate_map
```

For a new experiment, replace the example path with its specification. Use
`--formats png svg pdf` to select exports; these three formats are the default.
The helper also writes `.json`, `.csv` and `.manifest.json` beside the graphics.
It reads the input specification and never relocates its explicit assignments.

Regenerate the bundled worked example with:

```bash
python3 "$SKILL_DIR/scripts/build_mixed_example.py" /absolute/path/example.json
```

## Guardrails

- Support named 6-, 12-, 24-, 48-, 96- and 384-well formats, or explicit positive
  rectangular row/column geometry. Do not invent missing well assignments.
- Preserve user-fixed positions unless asked to rearrange. Hiding empty wells
  must never compact the occupied positions.
- Fit mixed formats to their panel by default. Use `plate_scale: "shared_pitch"`
  when the user wants equal well pitch across formats; these are schematic
  maps, not physical scale drawings.
- Keep category colors stable across all plates within each view. Generate
  legends from the explicit ordered definitions, with gray for empty wells.
- Keep complete multiline labels, or use explicit per-view overrides. Never
  derive biological identity by parsing a label.
- Keep experiment-specific logic in input builders, not the renderer. Allocate
  positions first and save the resulting explicit records for every view.
- Preserve the exported specification hash, renderer hash, font hash, software
  versions, counts and text-fit results. Byte stability is environment-specific.
- Treat continuous scales, extra facet dimensions, pagination, spreadsheet
  import, replicate outlines and automatic abbreviation as future extensions.
  Confirm practical export size before rendering dozens of plates or dense
  384-well labels.

## Verification

```bash
python3 "$SKILL_DIR/scripts/check_engine.py"
```

This checks the worked assignments, rejected inputs, preset geometry, mixed
formats, interior-first capacity, adjacent pairs, text overflow, circular wells
and repeat-export byte equality. Keep smoke-test outputs separate from user
experiment outputs.
