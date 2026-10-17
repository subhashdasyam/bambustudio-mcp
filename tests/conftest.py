import json
import zipfile
from pathlib import Path

import pytest

from bambustudio_mcp.project import CORE, SETTINGS, MODEL_CONFIG
from bambustudio_mcp.runtime import Runtime
from bambustudio_mcp.service import Service


@pytest.fixture
def project_file(tmp_path):
    path = tmp_path / "part.3mf"
    model = f'''<model xmlns="{CORE}" unit="millimeter"><resources>
    <object id="1" type="model"><mesh><vertices>
    <vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/>
    <vertex x="0" y="10" z="0"/><vertex x="0" y="0" z="10"/>
    </vertices><triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>
    <triangle v1="1" v2="2" v3="3"/><triangle v1="2" v2="0" v3="3"/></triangles></mesh></object>
    <object id="2" type="model"><components><component objectid="1"/></components></object>
    </resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 20 30 0"/></build></model>'''
    config = '<config><object id="2"><metadata key="name" value="test part"/><part id="1" subtype="normal_part"/></object><plate><metadata key="plater_id" value="1"/></plate></config>'
    settings = {"wall_loops": "2", "layer_height": "0.2", "sparse_infill_density": "15%", "top_shell_layers": "3",
                "enable_support": "0", "spiral_mode": "0", "nozzle_diameter": ["0.4"],
                "filament_density": ["1.24"], "min_layer_height": ["0.08"], "max_layer_height": ["0.28"]}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr(SETTINGS, json.dumps(settings))
        archive.writestr(MODEL_CONFIG, config)
        archive.writestr("Metadata/custom.bin", b"preserve\x00me")
        archive.writestr("custom/texture.png", b"preserve png")
        archive.writestr("Metadata/plate_1.gcode", b"stale gcode")
        archive.writestr("Metadata/plate_1.png", b"stale preview")
    return path


@pytest.fixture
def fake_runtime(tmp_path):
    executable = tmp_path / "studio"
    executable.write_text('''#!/usr/bin/env python3
import json, pathlib, sys, time
args=sys.argv[1:]
if '--help' in args:
 pathlib.Path('result.json').write_text('{}')
 print('BambuStudio-02.08.04.57:'); sys.exit(0)
if '--slice' in args:
 if pathlib.Path(__file__+'.sleep').exists(): time.sleep(60)
 pathlib.Path('sliced.3mf').write_bytes(pathlib.Path('input.3mf').read_bytes())
 pathlib.Path('effective-settings.json').write_text('{}')
 pathlib.Path('result.json').write_text(json.dumps({'return_code':0, 'sliced_plates':[{'total_predication':60,'filaments':[{'total_used_g':2}]}]}))
''')
    executable.chmod(0o755)
    return Runtime(str(executable))


@pytest.fixture
def service(tmp_path, fake_runtime):
    return Service(tmp_path / "workspace", fake_runtime)


@pytest.fixture
def opened(service, project_file):
    return service.open_project(str(project_file))
