# Print preparation procedure

1. Inspect the project, version, machine/nozzle profiles, filament assignments and current settings.

2. Confirm the filament material, brand, exact product/variant, color, drying history and calibration.
   Ask only for missing details. Do not infer the actual spool from a saved profile.
   Obtain missing nozzle, load, attachment, temperature, dimension, surface and time requirements.
   Preserve unknowns. Do not infer loads from shape.

   Confirm the printer, installed nozzle and affected surface. Then read `bambustudio://quality-guide`.
   Record firmware, build plate, enclosure conditions, intended use and each spool's feeding path.
   Distinguish automatic feeding from dry storage with a bypass. Check model-specific accessory support.
   Select the matching concern: threads, visible walls, seams, tops, support contacts, dimensional fit, joints, fine detail or flexible feed.
   Obtain the user's physical observations before a remedy. Use only rules that match the actual setup.

3. Analyze geometry and review previews. Locate thin connections, overhangs, contact areas and likely
   stress concentrations. Label these as geometric observations or engineering hypotheses.
   Run `analyze_geometry` on the exact revision before each slice. Show its highlighted SVG previews.
   Explain each issue ID, severity, measured value, threshold, CAD fix and applicable slicer controls.
   Review coverage limits. An empty heuristic report does not establish a safe or attractive print.

   Ask about moving parts, hinges, holes that must fit, cosmetic faces and load direction.
   Record these requirements with `record_print_context`. Declare cosmetic regions in world coordinates.
   Use `print_in_place`, `holes_lying_flat`, `bed_contact` and `bridges` where applicable.
   For a tight moving joint, suggest a small CAD or manual Studio cut-out before a long print.
   Preserve both mating surfaces, the axis, clearance, orientation and local process in that coupon.

4. Consider orientation and layer adhesion before adding material. Compare wall thickness, shells and
   local reinforcement with infill changes. Increasing infill is not a universal strength improvement.

5. Address top, side, supported and underside finish separately. Ironing affects top surfaces.
   Smaller layers affect stepping. Support interfaces affect supported surfaces. Each has time/material tradeoffs.

6. Respect calibrated temperature, cooling, speed and volumetric flow limits. Machine motion limits are
   constraints. A material datasheet for moulded material does not establish printed interlayer strength.

7. Explain each proposed setting: affected region, observation, reason, expected benefit, cost, evidence
   and uncertainty. Use the existing model and preserve functional dimensions.

8. Compare a bounded set of candidates against the same baseline. Report actual slicer estimates,
   warnings and effective values. Keep alternatives when neither wins on every required objective.
   Treat time differences below the comparison's 1% threshold as inconclusive. For important small
   differences, repeat the baseline and candidates to measure variability before selecting a winner.
   Read `unresolved_geometry_issues` from slice/export results. Warnings do not block the operation.
   Use `inspect_slice` or job inspection to link actual paths to the geometry issue IDs.
   Distinguish commanded extrusion in nominal gaps from physical fusion or sag.
   Review bridge lengths/directions, cosmetic seam candidates, top-crossing travel and sampled ironing coverage.

9. Export a new project and review it in Studio. Design a physical baseline/candidate comparison using
   repeated prints, duration measurements, dimensional checks, defined finish inspection and loads
   representative of intended use. Report strength and finish as unverified until those results exist.

## Quality experiments

The quality guide contains sourced rules, existing setting keys, scope limits and physical acceptance checks.
It supplies candidate experiments. It does not score finish or establish a universal material recipe.

1. Read the current values with `get_settings`. Check types and scopes with `list_options`.
2. Check material dryness and nozzle condition before calibration.
3. Keep flow ratio, flow dynamics K and maximum volumetric flow separate.
4. Use the printer's supported calibration procedure. P1S requires manual flow dynamics calibration.
   Generic pressure-advance fields do not implement K calibration for Bambu printers.
5. Apply one deliberate candidate in a new revision. Reuse supported local edits where applicable.
6. Review actual slice output and normalized settings. Separate cleanup temperature from object-extrusion temperature.
   Requested speed and emitted feed commands do not establish actual attained speed.
7. Compare a small physical specimen with the same critical geometry, orientation, material and local process.
   A plain hole does not validate a helical thread. Define fit and surface criteria before the trial.
8. Record the setup, revision and physical observations. Keep fit, finish and strength unverified without those results.

Check the quality guide before each experiment. A zero small-perimeter threshold disables its slowdown.
The pinned threshold represents equivalent radius, not hole diameter. Inner-outer-inner requires at least three walls.
Wall sequence, ironing activation and XY hole compensation are project-only in the current catalog.
These controls can affect other surfaces. Inspect those surfaces before a full print.

## Curves, detail, flexible feed and support removal

These candidates apply to P1S and other Bambu printers within their confirmed hardware limits.

1. For steps on shallow curves, compare an upright orientation with the baseline.
   Check support contact, build height, bed stability and load direction after rotation.
   Treat dullness from material, temperature or flow as a separate diagnosis. Gloss does not prove strength.
2. For coarse detail, compare finer layers within the installed nozzle limits.
   A 0.2 mm nozzle requires a physical swap and compatible profiles and filament.
   Do not copy 0.8, 0.6 or 0.4 mm nozzle diameters into layer height.
3. For stepped domes, compare local refinement through a variable layer profile.
   Studio provides Adaptive in its Variable Layer Height tool. This MCP writes explicit profiles through `layer_heights`.
   Check organic-support restrictions, prime-tower compatibility and actual slice time.
4. For flexible filament strings, check dryness before feed resistance.
   Compare a compatible low-resistance external path when the model-specific procedure permits it.
   A top holder needs verified fit, free spool rotation and full toolhead clearance.
   Do not universally disconnect PTFE. Do not copy another printer's load procedure.
5. For support scars, compare orientation and same-material interface settings first.
   P1S has one nozzle. Another interface material requires compatible material changes, feeder, purge and both products.
   A manual top spool does not give ordinary soft TPU automatic material changes.
   On confirmed multi-nozzle hardware, verify the model/support pair and physical nozzle assignment.
   A filament index is not a physical nozzle ID. Check removal quality and model adhesion separately.

The demonstration supplied by the user is a source of candidate remedies. Its clip and exact setup are unavailable.
Use the linked manufacturer procedures to check applicability. Record physical results before a quality claim.

## Filament specifications and Generic fallback

Complete these steps before print recommendations or slices.

1. Use the client's web search to find official manufacturer specifications for the exact product/variant.
   Cite the source URLs. Check nozzle and bed temperatures, drying instructions, cooling and print speed limits.
   Do not substitute another product or variant, such as Rapid PETG for standard PETG.
   Do not convert a linear speed rating into a volumetric flow limit without calibration.

2. Use `list_profiles` to find a filament profile compatible with the printer and nozzle.
   If exact specifications cannot be verified or web search is unavailable, select a matching Generic material profile.
   For example, use Generic PETG for confirmed standard PETG without verified specifications.
   If the material is unknown or no compatible Generic profile exists, ask the user before you continue.

3. Identify values from manufacturer specifications, profiles and calibration.
   Label Generic values as unverified starting points. Keep any known limits from the spool label.
   Do not invent a brand profile or treat another brand's profile as a verified match.

4. Check saved filament settings against the confirmed spool before you slice.
   Correct mismatched values in a new revision. Preserve the original project.
   Use calibration and a small physical sample to check thread fit and surface finish before a full print.

The client supplies web search. The MCP server supplies local profiles and project tools.

## Geometry and G-code evidence

The analyzer checks nominal layer sections and facing-boundary scan lines without a polygon-engine dependency.
Thresholds come from the saved nozzle, line width, layer height and initial-layer settings.
Two line widths in XY and two layer heights in Z are conservative review thresholds.
The narrowest gaps receive a higher risk rank. A 0.5 mm gap can retain an advisory without a high-risk failure.
No source certifies a universal moving clearance. Use a calibrated coupon for the actual joint.

Elephant-foot compensation is not measured expansion. Record measured per-side expansion when available.
Otherwise, the report labels its initial-line-width proxy budget explicitly.
STL facets cannot prove that a small angular step is an intentional stop or an exported smooth curve.

G-code correlation adds the archived extruder offset to emitted XY coordinates when all possible offsets agree.
Differing extruder offsets and unmodeled plate origins remain unknown.
Outer-wall starts after travel are seam candidates. They do not establish an exact physical seam.
Both geometry and slice tools return compact summaries with a full `report_path`. Read shared guidance and slicer-setting tables.
Preview paths appear once; append `#<issue id>` to highlight an issue. Raw indices, probes, samples and paths stay in the saved reports.
Clearance results estimate bead edges using emitted line width or saved feature/first-layer width and actual layer height.
Review margins below 0.1 mm and overlap candidates. Positive margins do not rule out actual spread, roof sag or fusion.
Bridge matching includes the bead footprint and the emitted layer above a geometric ceiling.
Top-travel observations exclude Z-hop and later-covered footprints, and link only to top or cosmetic issues.
Shared observations can appear under several issue IDs. Use unique path totals to avoid counting them twice.
Ironing coverage checks sampled intended top points with the archived spacing. It does not prove every point is smooth.

## Primary references

- BambuStudio source: https://github.com/bambulab/BambuStudio
- Perimeters and shells: https://help.prusa3d.com/article/layers-and-perimeters_1748
- Supports and orientation: https://help.prusa3d.com/article/support-material_1698
- Top surface ironing: https://blog.prusa3d.com/make-top-surfaces-super-smooth-ironing-prusaslicer-2-3-beta_41506/

These references provide principles. They do not provide calibrated recommendations for an unknown part/material.
