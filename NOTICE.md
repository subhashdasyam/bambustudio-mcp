# Third-party notices

The configuration catalog derives from BambuStudio configuration definitions at source revision `79df7ae30`.

Upstream project: https://github.com/bambulab/BambuStudio

BambuStudio derives from PrusaSlicer and Slic3r. Its source and the derived configuration material use the GNU Affero General Public License, version 3 or later. This repository uses the same license. See `LICENSE` for the license text. Original notices remain in the upstream source files.

The painting representation follows the serialization in `FacetsAnnotation` and `TriangleSelector`. The project adapter follows BambuStudio's 3MF importer and exporter. The implementation does not distribute a Studio executable.

The MCP Python SDK, NumPy, Trimesh, Rtree, and defusedxml retain their respective licenses. The locked dependency set records their exact versions.

The preset test fixtures are original BambuStudio process and filament resources from the same pinned source revision.
Their paths, source URLs and checksums are recorded in `tests/fixtures/preset_profiles/manifest.json` under the upstream AGPL license.
The reusable-preset capability was independently implemented after reviewing the behavior described in
https://github.com/unf0rg0tt3n/bambustudio-mcp/commit/27177d75923ce59d4b13c5222b0ad1f253e034cd.
