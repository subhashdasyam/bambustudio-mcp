import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bambustudio_mcp.presets import Presets

PROCESS = "0.20mm Standard @BBL X1C"
PETG = "Generic PETG HF @BBL X1C"
SUPPORT = "Bambu Support For PLA/PETG @BBL X1C"
FIXTURES = Path(__file__).parent / "fixtures/preset_profiles"


@pytest.fixture
def preset_store(tmp_path):
    vendor = tmp_path / "vendor"
    shutil.copytree(FIXTURES, vendor)
    config = {"targets": {}}
    for name, layout in (("desktop", "desktop"), ("api", "api")):
        root = tmp_path / name
        for folder in (("process", "filament") if layout == "desktop" else ("presets", "filaments")):
            (root / folder).mkdir(parents=True)
        config["targets"][name] = {"root":str(root),"system_roots":[str(vendor)],"writable":True,"layout":layout}
    return Presets(config), config, vendor


def create(api, target="desktop", kind="process", name="My process", base=PROCESS, changes=None):
    return api.run(target,"create",kind=kind,base=base,name=name,changes=changes or {},dry_run=False)


def test_fixture_provenance():
    for name, item in json.loads((FIXTURES/"manifest.json").read_text()).items():
        assert hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest()==item['sha256']
        assert 'bambulab/BambuStudio/79df7ae' in item['url']


def test_process_derivation_preview_create_and_compact_read(preset_store):
    api, config, _ = preset_store
    root=Path(config['targets']['desktop']['root'])
    before=sorted(root.rglob('*'))
    preview=api.run('desktop','derive',kind='process',base=PROCESS,name='My process',changes={'wall_loops':4,'enable_support':True})
    assert sorted(root.rglob('*'))==before
    result=api.run('desktop','create',kind='process',base=PROCESS,name='My process',changes={'wall_loops':4,'enable_support':True},dry_run=False,expected_base_revision=preview['base_revision'])
    raw=json.loads((root/'process/My process.json').read_text())
    assert raw['inherits']==PROCESS and raw['from']=='User' and raw['print_settings_id']=='My process'
    assert raw['wall_loops']=='4' and raw['enable_support']=='1'
    assert 'machine_start_gcode' not in raw and 'setting_id' not in raw
    read=api.run('desktop','read',kind='process',name='My process',keys=['wall_loops','layer_height'])
    assert read['settings']['wall_loops']=='4' and read['revision']==result['revision']
    assert api.run('desktop','read',kind='process',name='My process')['settings']=={}
    assert api.run('desktop','validate',kind='process',name='My process')['catalog_compatible']
    with pytest.raises(ValueError,match='already exists'):
        create(api)


def test_filament_material_identity_backup_exact_restore_and_stale_guard(preset_store):
    api, config, _=preset_store
    created=create(api,kind='filament',base=SUPPORT,name='My support',changes={'nozzle_temperature':[230]})
    path=Path(config['targets']['desktop']['root'])/'filament/My support.json'
    original=path.read_bytes();data=json.loads(original)
    assert data['filament_settings_id']==['My support'] and isinstance(data['filament_id'],str)
    changed=api.run('desktop','update',kind='filament',name='My support',changes={'nozzle_temperature':[235]},expected_revision=created['revision'],dry_run=False)
    assert api.run('desktop','read',kind='filament',name='My support',keys=['nozzle_temperature'])['settings']['nozzle_temperature']==['235']
    with pytest.raises(ValueError,match='Stale'):
        api.run('desktop','update',kind='filament',name='My support',changes={'nozzle_temperature':[240]},expected_revision=created['revision'],dry_run=False)
    backups=api.run('desktop','backups',kind='filament',name='My support')
    assert backups['backups']==[{'id':created['revision']}]
    api.run('desktop','restore',kind='filament',name='My support',backup_id=changed['backup_id'],expected_revision=changed['revision'],dry_run=False)
    assert path.read_bytes()==original
    assert api.run('desktop','backups',kind='filament',name='My support')['total']==2


def test_api_layout_materializes_ancestry_and_does_not_touch_other_target(preset_store):
    api,config,_=preset_store
    create(api,target='api',kind='filament',base=PETG,name='Fast PETG',changes={'nozzle_temperature':[245]})
    raw=json.loads((Path(config['targets']['api']['root'])/'filaments/Fast PETG.json').read_text())
    assert raw['inherits']=='' and raw['nozzle_temperature']==['245'] and len(raw)>30
    assert raw['filament_settings_id']==['Fast PETG'] and 'setting_id' not in raw
    assert not list(Path(config['targets']['desktop']['root']).rglob('*.json'))


def test_readonly_dry_run_creates_no_directories_locks_or_backups(preset_store):
    api,config,_=preset_store
    config['targets']['desktop']['writable']=False
    root=Path(config['targets']['desktop']['root']);before=sorted(root.rglob('*'))
    assert api.run('desktop','create',kind='process',base=PROCESS,name='Preview',changes={})['dry_run']
    assert sorted(root.rglob('*'))==before
    with pytest.raises(ValueError,match='read-only'):
        create(api)
    assert sorted(root.rglob('*'))==before


@pytest.mark.parametrize('name',['../escape','a/b','a\\b','.hidden',' leading','trailing ','bad\nname'])
def test_unsafe_new_names(preset_store,name):
    api,_,_=preset_store
    with pytest.raises(ValueError,match='names'):
        create(api,name=name)


@pytest.mark.parametrize('key,value',[('machine_start_gcode','G28'),('nozzle_diameter',[.8]),('support_interface_filament',2),('nozzle_temperature',240),('nozzle_temperature',[float('nan')]),('nozzle_temperature',[-10])])
def test_material_changes_have_typed_category_and_command_boundaries(preset_store,key,value):
    api,_,_=preset_store
    with pytest.raises(ValueError):
        create(api,kind='filament',base=PETG,name='Bad',changes={key:value})


def test_missing_parent_cycle_duplicate_name_and_ancestry_guard(preset_store):
    api,config,vendor=preset_store
    root=Path(config['targets']['desktop']['root'])/'process'
    (root/'missing.json').write_text(json.dumps({'type':'process','name':'missing','inherits':'absent'}))
    with pytest.raises(ValueError,match='Missing'):
        api.run('desktop','read',kind='process',name='missing')
    (root/'missing.json').unlink()
    for name,parent in [('a','b'),('b','a')]:
        (root/(name+'.json')).write_text(json.dumps({'type':'process','name':name,'inherits':parent}))
    with pytest.raises(ValueError,match='cycle'):
        api.run('desktop','read',kind='process',name='a')
    (root/'a.json').unlink();(root/'b.json').unlink()
    preview=api.run('desktop','derive',kind='process',base=PROCESS,name='Guarded',changes={})
    path=vendor/'process'/(PROCESS+'.json');data=json.loads(path.read_text());data['version']='changed';path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='ancestry'):
        api.run('desktop','create',kind='process',base=PROCESS,name='Guarded',changes={},expected_base_revision=preview['base_revision'],dry_run=False)
    (root/'duplicate.json').write_text(path.read_text())
    with pytest.raises(ValueError,match='Ambiguous'):
        api.run('desktop','list',kind='process')


@pytest.mark.parametrize('link',['symlink','hardlink','fifo'])
def test_nonregular_or_linked_preset_files_are_rejected(preset_store,tmp_path,link):
    api,config,_=preset_store
    source=tmp_path/'outside.json';source.write_text('{}')
    destination=Path(config['targets']['desktop']['root'])/'process/unsafe.json'
    if link=='symlink':destination.symlink_to(source)
    elif link=='hardlink':os.link(source,destination)
    else:os.mkfifo(destination)
    with pytest.raises((ValueError,OSError)):
        api.run('desktop','list',kind='process')
    assert source.read_text()=='{}'


def test_root_symlink_and_backup_escape_are_rejected(preset_store,tmp_path):
    api,config,_=preset_store
    original=Path(config['targets']['desktop']['root']);alias=tmp_path/'alias';alias.symlink_to(original,target_is_directory=True)
    config['targets']['desktop']['root']=str(alias)
    with pytest.raises(OSError):api.run('desktop','list',kind='process')
    config['targets']['desktop']['root']=str(original)
    created=create(api)
    outside=tmp_path/'backups';outside.mkdir();(original/'process/.preset-backups').symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError):
        api.run('desktop','update',kind='process',name='My process',changes={'wall_loops':5},expected_revision=created['revision'],dry_run=False)
    assert not list(outside.iterdir())


def test_concurrent_creators_do_not_overwrite(preset_store):
    api,_,_=preset_store
    def attempt(_):
        try:return create(api)['written']
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(attempt,range(2)))==[False,True]


def prepared_project(service, project_file, tmp_path, printer='Bambu Lab P1S 0.4 nozzle'):
    from bambustudio_mcp.project import Project
    project=Project(project_file)
    project.settings.update(printer_settings_id=printer,filament_settings_id=['Existing PLA','Existing PETG'],
                            filament_type=['PLA','PETG'],nozzle_temperature=['210','250'])
    project.dirty_settings=True
    path=tmp_path/'prepared.3mf';project.save(path)
    return service.open_project(str(path)),path


def test_apply_preset_previews_new_revision_preserves_source_and_records_origin(preset_store,service,project_file,tmp_path):
    api,_,_=preset_store;service.presets=api
    create(api,changes={'wall_loops':5})
    opened,path=prepared_project(service,project_file,tmp_path);before=path.read_bytes()
    args=dict(project_id=opened['project_id'],revision=opened['revision'],target='desktop',kind='process',name='My process',keys=['wall_loops'])
    preview=service.apply_preset(**args)
    assert preview['dry_run'] and preview['changes'][0]['requested']==5
    assert service.inspect_project(opened['project_id'])['head']==opened['revision'] and path.read_bytes()==before
    result=service.apply_preset(**args,dry_run=False,expected_preset_revision=preview['preset_source']['resolution_revision'])
    assert result['revision']!=opened['revision'] and path.read_bytes()==before
    assert service.get_settings(opened['project_id'],keys=['wall_loops'])['settings']['wall_loops']['value']==5
    record=json.loads((service._directory(opened['project_id'])/(result['revision']+'.json')).read_text())
    assert record['changes'][-1]['preset_source']==result['preset_source']
    with pytest.raises(ValueError,match='Stale project'):
        service.apply_preset(**args,dry_run=False)


def test_material_apply_preserves_other_slots_and_rejects_incompatible_printers(preset_store,service,project_file,tmp_path):
    api,_,_=preset_store;service.presets=api
    create(api,kind='filament',base=PETG,name='My PETG',changes={'nozzle_temperature':[245]})
    opened,_=prepared_project(service,project_file,tmp_path,printer='Bambu Lab X1 Carbon 0.4 nozzle')
    args=dict(project_id=opened['project_id'],revision=opened['revision'],target='desktop',kind='filament',name='My PETG',keys=['nozzle_temperature'])
    with pytest.raises(ValueError,match='mapping'):
        service.apply_preset(**args)
    result=service.apply_preset(**args,filament_index=1,dry_run=False)
    assert service.get_settings(opened['project_id'],keys=['nozzle_temperature'])['settings']['nozzle_temperature']['value']==[210,245]
    wrong,_=prepared_project(service,project_file,tmp_path,printer='Bambu Lab A1 0.4 nozzle')
    with pytest.raises(ValueError,match='compatible'):
        service.apply_preset(**{**args,'project_id':wrong['project_id'],'revision':wrong['revision']},filament_index=0)


def test_apply_rejects_nozzle_layer_limits_and_changed_preset(preset_store,service,project_file,tmp_path):
    api,config,_=preset_store;service.presets=api
    made=create(api,changes={'layer_height':.4})
    opened,_=prepared_project(service,project_file,tmp_path)
    args=dict(project_id=opened['project_id'],revision=opened['revision'],target='desktop',kind='process',name='My process',keys=['layer_height'])
    with pytest.raises(ValueError,match='nozzle-profile limits'):
        service.apply_preset(**args)
    token=api.run('desktop','read',kind='process',name='My process')['resolution_revision']
    api.run('desktop','update',kind='process',name='My process',changes={'layer_height':.12},expected_revision=made['revision'],dry_run=False)
    with pytest.raises(ValueError,match='Stale preset ancestry'):
        service.apply_preset(**args,expected_preset_revision=token)
    path=Path(config['targets']['desktop']['root'])/'process/My process.json'
    data=json.loads(path.read_text());data['compatible_prints']=['A different process'];path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Saved process profile'):
        service.apply_preset(**args)
    data.pop('compatible_prints');data['compatible_printers_condition']='unverified expression';path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Conditional compatibility'):
        service.apply_preset(**args)


def test_bounded_agent_and_ssh_transport_real_subprocess_roundtrip(preset_store,tmp_path,monkeypatch):
    api,config,_=preset_store
    config_path=tmp_path/'remote targets;$(touch marker).json';config_path.write_text(json.dumps(config))
    executable=tmp_path/'ssh'
    executable.write_text('#!'+sys.executable+'\nimport os,shlex,sys\nargs=shlex.split(sys.argv[-1])\nassert args[:3]==["python3","-m","bambustudio_mcp.preset_agent"]\nassert "StrictHostKeyChecking=yes" in sys.argv\nos.execv(sys.executable,[sys.executable,*args[1:]])\n')
    executable.chmod(0o700)
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    remote=Presets({'targets':{'workstation':{'transport':'ssh','host':'fixture-host','remote_config':str(config_path),
                                             'remote_target':'desktop','layout':'desktop','writable':True}}})
    made=create(remote,target='workstation',name='Remote process',changes={'wall_loops':4})
    assert remote.run('workstation','read',kind='process',name='Remote process',keys=['wall_loops'])['settings']=={'wall_loops':'4'}
    with pytest.raises(ValueError,match='Stale'):
        remote.run('workstation','update',kind='process',name='Remote process',changes={'wall_loops':5},expected_revision='0'*64,dry_run=False)
    assert made['written']
    assert not (tmp_path/'marker').exists()
    response=subprocess.run([sys.executable,'-m','bambustudio_mcp.preset_agent','--config',str(config_path)],
                            input=json.dumps({'target':'desktop','layout':'desktop','action':'__dict__','kind':'process'}),capture_output=True,text=True)
    assert response.returncode==1 and 'Unknown' in json.loads(response.stdout)['error']


def test_ssh_config_cannot_choose_commands_or_forward_again(preset_store):
    from bambustudio_mcp.preset_agent import dispatch
    api,_,_=preset_store
    remote={'transport':'ssh','host':'fixture-host','remote_config':'/etc/mcp/targets.json','remote_target':'desktop','layout':'desktop'}
    configured=Presets({'targets':{'remote':remote}},local_only=True)
    with pytest.raises(ValueError,match='cannot forward'):
        configured.run('remote','list',kind='process')
    with pytest.raises(ValueError,match='local target'):
        dispatch(configured,{'target':'remote','layout':'desktop','action':'list','kind':'process'})
    for extra in ({'host':'-oProxyCommand=bad'},{'command':'touch unwanted'},{'password':'secret'}):
        malformed=Presets({'targets':{'remote':{**remote,**extra}}})
        with pytest.raises(ValueError):malformed.list_targets()


@pytest.mark.asyncio
async def test_preset_mcp_stdio_and_bad_optional_config_does_not_kill_core(preset_store,tmp_path,fake_runtime):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    _,config,_=preset_store
    path=tmp_path/'targets.json';path.write_text(json.dumps(config))
    params=StdioServerParameters(command=sys.executable,args=['-m','bambustudio_mcp.server'],env={**os.environ,
        'BAMBUSTUDIO_WORKSPACE':str(tmp_path/'mcp'),'BAMBUSTUDIO_EXECUTABLE':str(fake_runtime.executable),
        'BAMBUSTUDIO_PRESET_TARGETS':str(path)})
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools=await client.list_tools()
            assert len(tools.tools)==29 and {'apply_preset','derive_preset','restore_preset'}<={tool.name for tool in tools.tools}
            preview=await client.call_tool('create_preset',{'target':'desktop','kind':'process','base':PROCESS,'name':'MCP process','changes':{'wall_loops':3}})
            assert not preview.isError and json.loads(preview.content[0].text)['dry_run']
            made=await client.call_tool('create_preset',{'target':'desktop','kind':'process','base':PROCESS,'name':'MCP process','changes':{'wall_loops':3},'dry_run':False})
            assert not made.isError and len(made.model_dump_json().encode())<60_000
            found=await client.call_tool('read_preset',{'target':'desktop','kind':'process','name':'MCP process','keys':['wall_loops']})
            assert found.structuredContent['settings']=={'wall_loops':'3'}
            path.write_text('invalid JSON')
            assert (await client.call_tool('list_preset_targets',{})).isError
            assert not (await client.call_tool('list_options',{'query':'wall_loops'})).isError


@pytest.mark.parametrize('raw',['{"name":"a","name":"b"}','{"value":NaN}'])
def test_duplicate_and_nonfinite_json_are_rejected(preset_store,raw):
    api,config,_=preset_store
    (Path(config['targets']['desktop']['root'])/'process/invalid.json').write_text(raw)
    with pytest.raises(ValueError):api.run('desktop','list',kind='process')


def test_ancestry_expansion_is_bounded(preset_store,monkeypatch):
    api,config,_=preset_store
    folder=Path(config['targets']['desktop']['root'])/'process'
    for i in range(13):
        data={'name':f'branch{i}','type':'process'}
        if i:data['include']=[f'branch{i-1}']*2
        (folder/f'branch{i}.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='budget'):
        api.run('desktop','read',kind='process',name='branch12')
    monkeypatch.setattr('bambustudio_mcp.presets.MAX_INDEX_BYTES',100)
    with pytest.raises(ValueError,match='input budget'):
        api.run('desktop','list',kind='process')


def test_compatibility_only_narrows_and_corrupt_backups_are_refused(preset_store):
    api,config,_=preset_store
    allowed=api.run('desktop','read',kind='process',name=PROCESS)['compatible_printers']
    made=create(api,changes={'compatible_printers':allowed[:1]})
    with pytest.raises(ValueError,match='narrow'):
        api.run('desktop','update',kind='process',name='My process',changes={'compatible_printers':allowed},expected_revision=made['revision'])
    changed=api.run('desktop','update',kind='process',name='My process',changes={'wall_loops':4},expected_revision=made['revision'],dry_run=False)
    folder=Path(config['targets']['desktop']['root'])/'process/.preset-backups'
    next(folder.rglob(made['revision']+'.json')).write_text('{}')
    with pytest.raises(ValueError,match='checksum'):
        api.run('desktop','restore',kind='process',name='My process',backup_id=changed['backup_id'],expected_revision=changed['revision'])


@pytest.mark.skipif(not os.environ.get('BAMBUSTUDIO_TEST_MODEL'),reason='Live Studio/model paths not configured')
def test_real_studio_applies_reusable_process_without_changing_source(preset_store,tmp_path):
    from bambustudio_mcp.project import Project
    from bambustudio_mcp.runtime import Runtime
    from bambustudio_mcp.service import Service
    from test_live import wait
    api,_,_=preset_store
    create(api,changes={'wall_loops':4,'sparse_infill_density':12})
    service=Service(tmp_path/'preset-live',Runtime());service.presets=api
    source=Path(os.environ['BAMBUSTUDIO_TEST_MODEL']);before=source.read_bytes()
    opened=service.open_project(str(source),'Bambu Lab P1S 0.4 nozzle',PROCESS,['Bambu PLA Basic @BBL P1S 0.4 nozzle'])
    changed=service.apply_preset(opened['project_id'],opened['revision'],'desktop','process','My process',
                                 ['wall_loops','sparse_infill_density'],dry_run=False)
    job=wait(service,service.start_slice(opened['project_id'],changed['revision']))
    output=Project(Path(job['directory'])/'sliced.3mf')
    assert output.settings['wall_loops']=='4' and float(output.settings['sparse_infill_density'].rstrip('%'))==12
    assert source.read_bytes()==before
