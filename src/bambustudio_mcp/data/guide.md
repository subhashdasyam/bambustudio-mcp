# Print preparation procedure

1. Inspect the project, version, machine/nozzle profiles, filament assignments and current settings.

2. Confirm the filament material, brand, exact product/variant, color, drying history and calibration.
   Ask only for missing details. Do not infer the actual spool from a saved profile.
   Obtain missing nozzle, load, attachment, temperature, dimension, surface and time requirements.
   Preserve unknowns. Do not infer loads from shape.

   Confirm the printer, installed nozzle and affected surface. Then read `bambustudio://quality-guide`.
   Record firmware, build plate, enclosure conditions, intended use and each spool's feeding path.
   Distinguish automatic feeding from dry storage with a bypass. Check model-specific accessory support.
   Select the matching concern: threads, visible walls, seams, tops, support contacts, dimensional fit or joints.
   Obtain the user's physical observations before a remedy. Use only rules that match the actual setup.

3. Analyze geometry and review previews. Locate thin connections, overhangs, contact areas and likely
   stress concentrations. Label these as geometric observations or engineering hypotheses.

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

## Primary references

- BambuStudio source: https://github.com/bambulab/BambuStudio
- Perimeters and shells: https://help.prusa3d.com/article/layers-and-perimeters_1748
- Supports and orientation: https://help.prusa3d.com/article/support-material_1698
- Top surface ironing: https://blog.prusa3d.com/make-top-surfaces-super-smooth-ironing-prusaslicer-2-3-beta_41506/

These references provide principles. They do not provide calibrated recommendations for an unknown part/material.
