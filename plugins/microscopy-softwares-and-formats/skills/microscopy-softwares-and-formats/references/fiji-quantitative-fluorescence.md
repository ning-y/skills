# Quantitative fluorescence in Fiji

## Establish comparability first

Before comparing intensity across ROIs, confirm from metadata or the acquisition
record that the relevant channel used compatible exposure, gain, illumination,
objective, bit depth, and processing. Identical exposure alone does not prove
full comparability, but differing exposure prevents a direct raw-intensity
comparison unless a justified calibration exists.

Keep measurements on unenhanced pixels. Use Fiji contrast enhancement and LUTs
only for QC previews.

## Excluding acellular or autofluorescent surface material

For cornified skin, whole-surface GFP can be dominated by DAPI-negative keratin
and autofluorescent debris. A conservative Fiji workflow is:

1. Define the tissue surface from DAPI rather than GFP so GFP-bright acellular
   material cannot define the mask.
2. Create a fixed physical-depth band inward from that DAPI-defined surface.
3. Segment DAPI-positive nuclear objects with background subtraction, an
   explicitly named threshold method, watershed, and particle-size filters in
   calibrated units.
4. Measure GFP only within the accepted DAPI-positive objects or a documented
   nucleus-centred neighborhood.
5. Estimate GFP background independently for each ROI from low-DAPI, non-tissue
   pixels and report the method and value.
6. Save the surface-band mask and a QC overlay showing accepted objects.
7. Repeat with a second plausible band depth to test sensitivity to the surface
   definition.

Describe the result as the “outer viable DAPI-positive layer” unless morphology
or an additional marker establishes keratinocyte/epidermal identity. DAPI alone
does not distinguish epidermal keratinocytes from every adjacent nucleated cell.

Measuring GFP only over nuclei is deliberately conservative and may not capture
all cytoplasmic or membrane signal. State this tradeoff rather than presenting
the number as whole-cell GFP.

## Avoiding pseudoreplication

Pixels and nuclei from one tissue region are not independent biological
replicates. For a within-image heterogeneity summary, aggregate accepted nuclei
into fixed-size spatial blocks before bootstrapping or permuting. Report the
number of nuclei and blocks, but label intervals and p-values as exploratory
image-level statistics.

One ROI versus one ROI can support this form of conclusion:

```text
ROI 4 is brighter than ROI 3 in these images under the stated mask definition.
```

It cannot establish a population-level treatment or genotype effect. That needs
independent biological samples and a model whose experimental unit is the
animal/specimen, not the pixel, nucleus, field, or ROI.

## QC deliverables

Save enough information to reproduce and challenge the result:

- source file paths and channel mapping;
- acquisition settings checked and any unavailable settings;
- pixel calibration and analysis scale;
- preprocessing, threshold method, particle-size range, and band depth;
- background values;
- accepted-object and spatial-block CSV tables;
- masks and channel-colored overlays;
- primary estimate, effect size, sensitivity analysis, and scope limitation.
