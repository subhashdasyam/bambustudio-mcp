# Print preparation procedure

1. Inspect the project, version, machine/nozzle profiles, filament assignments and current settings.
2. Ask for material grade and calibration, intended loads and directions, attachments, temperature,
   critical dimensions, visible surfaces and time limits. Preserve unknowns. Do not infer loads from shape.
3. Analyze geometry and review previews. Locate thin connections, overhangs, contact areas and likely
   stress concentrations. Label these as geometric observations or engineering hypotheses.
4. Consider orientation and layer adhesion before adding material. Compare wall thickness, shells and
   local reinforcement with infill changes. Increasing infill is not a universal strength improvement.
5. Address top, side, supported and underside finish separately. Ironing affects top surfaces; smaller
   layers affect stepping; support interfaces affect supported surfaces. Each has time/material tradeoffs.
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

## Primary references

- BambuStudio source: https://github.com/bambulab/BambuStudio
- Perimeters and shells: https://help.prusa3d.com/article/layers-and-perimeters_1748
- Supports and orientation: https://help.prusa3d.com/article/support-material_1698
- Top surface ironing: https://blog.prusa3d.com/make-top-surfaces-super-smooth-ironing-prusaslicer-2-3-beta_41506/

These are starting principles, not calibrated recommendations for an unknown part/material.
