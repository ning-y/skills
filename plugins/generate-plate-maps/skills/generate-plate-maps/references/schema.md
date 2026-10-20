# Plate-map schema v1

Use UTF-8 JSON. Run `validate_spec` before any rendering. Unknown structural keys fail; arbitrary experiment/plate metadata and categorical well attributes are permitted. Standard library validation is authoritative; no external schema package is required.

## Objects

| Object | Required fields | Optional fields |
|---|---|---|
| Root | `schema_version: 1`, `experiment`, nonempty `plates`, nonempty `views` | `style` |
| Experiment | `title` | `subtitle`, `footer`, `metadata` object |
| Plate | unique string `id`, `title`, `wells`, and either `format` or explicit `rows` / `columns` | `metadata` object |
| Well | `row`, integer `column` (1-based), `status`, `attributes` | `label`, `labels_by_view` |
| View | unique `id`, `name`, `field`, ordered `categories` | `legend`, `label_fields` |
| Category | unique string `value`, Matplotlib `color` | display `label` |
| Legend | none | `title`, `include_empty` (true), `include_unused` (true) |

For occupied wells, require a nonblank label and nonempty string attributes for every view's field. For empty wells require `attributes: {}`. Reserve category value `empty` for occupancy; do not insert it in a category list. Render empty wells with `style.empty_color`. Represent an unused well explicitly even if it will be hidden. Reject duplicates, missing positions, invalid coordinates and unknown view categories.

Use `attributes` for donor/sample, treatment, stain, dose, replicate ID, control type and other categorical variables. Do not invent unknown doses, antibody identities or replicate groups. Use strings with units for dose when categorical. Store quantitative metadata outside categorical attributes until continuous scales are implemented.

Example occupied well:

```json
{"row":"C","column":9,"status":"occupied",
 "attributes":{"donor":"ES345M","treatment":"CIS","stain":"2° only anti-mouse"},
 "label":"CIS\n2° only\nanti-mouse"}
```

Example view:

```json
{"id":"treatment","name":"treatment","field":"treatment",
 "categories":[{"value":"UT","color":"#C9D1DA"},
               {"value":"DOXO","color":"#EBA394"},
               {"value":"CIS","color":"#9BBBE0"}],
 "legend":{"title":"Treatment / category","include_empty":true}}
```

All views reuse the exact same physical records. Prefer a shared explicit `label` for cross-view comparison. Override labels with `labels_by_view: {"donor":"custom\ntext"}` or set a view's `label_fields: ["donor","treatment"]` to join those exact attributes with newlines. Overrides take priority. Do not derive treatment identity by parsing these labels.

## Geometry and style

Use named `format` presets: 6→2×3, 12→3×4, 24→4×6, 48→6×8, 96→8×12, 384→16×24. For example, a plate may specify `"format": 24` instead of `rows`/`columns`. `resolve_spec` expands presets to explicit geometry without adding or changing any well records. Conflicting explicit geometry is rejected. Custom rectangular geometries still use explicit rows/columns. Use `row_name(i)` for A..Z, AA.. labels. By default, mixed geometries fit their own complete grids inside equal-sized facet panels, so 24-well cells display larger than 96-well cells. This is a schematic layout, not a physical scale drawing. Set `plate_scale: "shared_pitch"` to retain the same well pitch and align grids at the upper left. Facet rows follow the views list; facet columns follow the plates list. Only these two facet dimensions are currently implemented.

| Style key | Default | Meaning |
|---|---|---|
| `cell_inches` | 0.65 | Well pitch for the largest grid; sets figure dimensions |
| `plate_scale` | fit | Fit each full grid to its panel, or use `shared_pitch` |
| `font_size`, `min_font_size` | 8.2, 6.0 | Well-label type size in points and fitting floor |
| `title_size`, `panel_title_size` | 20, 11 | Global and panel titles in points |
| `legend_font_size`, `legend_columns` | 10, 6 | Legend type size and maximum column count |
| `dpi` | 300 | PNG export resolution |
| `font_family` | DejaVu Sans | Required installed font; missing font fails |
| `well_shape` | rounded | `rounded` rectangles or `circle` |
| `show_empty` | true | Hide empty cells/text without changing coordinates |
| `empty_label` | empty | Label fallback for empty wells without explicit label |
| `empty_color` | #E9EDF1 | Empty cell fill |
| `empty_text_color` | #8B929B | Muted empty text |
| `border_color` | #B1BBC6 | Well borders and legend swatches |

Fit well text by measuring the rendered bounding box and reducing type in 0.2-point steps down to the configured minimum. Reject overflow instead of clipping. For circular wells, fit an inscribed rectangle so corners remain inside the circle. No automatic scientific abbreviation occurs. Legends use explicit category labels/colors; present unused categories by default, and always append the gray empty category unless disabled. Check narrow maps with several long legend entries: reduce `legend_columns`, enlarge the figure using `cell_inches`, or choose shorter explicit legend labels.

## Commands and outputs

```bash
python3 scripts/build_icc_example.py /absolute/path/example.json
python3 scripts/plate_maps.py /absolute/path/example.json --output-prefix /absolute/path/icc_map
```

Export `icc_map.png`, `.svg`, `.pdf`, `.json`, `.csv` and `.manifest.json`. The normalized JSON is the complete reusable specification. CSV is a long-format audit table, not yet an import format. Flatten categorical attributes to columns prefixed `attribute.`. Preserve literal multiline labels with standard CSV quoting. Keep per-view overrides in JSON.

SVG contains vector paths for fonts to preserve appearance without requiring recipients to have the font. PDF embeds TrueType fonts. Preserve text as data in JSON/CSV for editing. Set stable SVG IDs and omit PDF/SVG creation dates. Verify repeated exports in the same Python/Matplotlib/font environment; record versions and hashes in the manifest. Cross-environment pixel identity is not guaranteed.

## Extend without mixing concerns

Add input adapters that produce this canonical JSON rather than adding spreadsheet logic to the renderer. Add continuous scales as a separate view type with an explicit schema version. Add replicate outlines independently of categorical fill. Add pagination before scaling to dozens of plates. Never silently randomize positions, fill omitted wells or infer experimental controls.

## Conversational input and the mixed example

Accept experiment descriptions conversationally, then encode explicit records internally. Preserve all empty wells by default. For an unspecified placement with clear biological combinations, choose a simple reversible arrangement and state it; never invent extra replicates or assay-inapplicable controls.

The four-plate example uses the title “Bat macrophage senescence marker optimization”. Plates 1–2 are 96-well ICC layouts: donors ES345M, ES296F, ES363M, EsDermFib occupy rows C–F; columns 3–5 are UT p16/p21/gH2AX, columns 6–8 are drug p16/p21/gH2AX, and columns 9–10 are drug-treated secondary-only anti-mouse/anti-rabbit controls. Plate 1 drug is DOXO; Plate 2 drug is CIS, including both secondary-only controls. Each has 32 occupied and 64 empty wells.

Plates 3–4 are 24-well Sudan black layouts: UT occupies B2–B5; drug occupies C2–C5 (DOXO on Plate 3, CIS on Plate 4). Donors ES345M, ES296F, ES363M, EsDermFib align vertically in columns 2–5, respectively. Each has 8 occupied and 16 empty wells. No antibody secondary-only control is transferred to the Sudan black assay. Run `build_mixed_example.py` to regenerate `assets/four_plate_icc_sudan_black.json`.

## Internal placement policy

Prefer interior positions for new or explicitly rearranged layouts, to reduce evaporation and uneven-warming effects. Keep this rationale and allocation diagnostics out of the PDF, titles, captions and figure notes. Preserve all empty wells visually. Respect user-specified physical positions unless asked to move them.

Use `allocation.allocate_grouped_positions(rows, columns, n_groups, wells_per_group)` before constructing well records. It defaults to interior-first allocation; center horizontal blocks when possible, wrap groups across interior rows when required, then use peripheral wells only when interior capacity is exhausted. Preserve order within each group. If total occupancy exceeds capacity, raise an error. If a plate has no interior (e.g. 6 wells), use available positions. Explicit `prefer_interior=False` permits full-plate centered blocks when requested. The renderer consumes these resolved coordinates and never relocates them.

## Dose and timing annotations

Store supplied dose, exposure, fixation timing and reference markers as separate string attributes on treated wells. Include the concentration with units in their multiline labels. Keep full schedules in the multiline experiment `footer`; the renderer reserves height for every footer line. Do not assign an invented dose to UT wells. Reference markers are user-supplied identifiers, not verified bibliographic citations.

The worked example uses DOXO 500 nM, a 24 h pulse on D0, and fixation on D10–12 [1]; CIS 1 µg/mL for 5 days followed by fixation [2]. The same annotations apply to each drug-treated secondary-only control and the corresponding Sudan black plate.
