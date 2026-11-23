from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
try:
    import pytest
except ImportError:
    class _PytestMock:
        class raises:
            def __init__(self, expected_exception):
                self.expected = expected_exception
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is None:
                    raise AssertionError(f"Expected {self.expected.__name__} but no exception was raised")
                return issubclass(exc_type, self.expected)
    pytest = _PytestMock()

from truth_seeker.model import validate,ValidationError,provenance,FILES,dump_records,load_generation
from truth_seeker.store import create_run,commit_generation,latest_good,resume,add_task,update_task,read_progress,read_manifest,update_manifest,sanitize_folder_theme,generate_run_folder_name
from truth_seeker.render import layout,emit,check_scene
from truth_seeker.qa import classify
from truth_seeker.repair import proposal,apply,RepairError


def sample(run='run_sample'):
    p=provenance(run)
    ev={'id':'evi_first','schema_version':'0.5','title':'検証用の架空資料','source_url':None,'locator':'第1章','excerpt_or_summary':'架空の例示','source_type':'other','access_status':'unknown','accessed_at':None,'independence_group':None,'rights_note':None,'provenance':p}
    cl={'id':'claim_origin','schema_version':'0.5','statement':'説明のための架空の年代','subject_refs':[{'kind':'node','id':'node_old'}],'supporting_evidence_ids':['evi_first'],'contradicting_evidence_ids':[],'assessment':'hypothetical','adoption_status':'adopted','adoption_reason':'仮説であることを明示','verification':{},'related_claim_ids':[],'provenance':p}
    def node(id,label,year,status='adopted'):
        return dict(id=id,schema_version='0.5',type='concept',label=label,aliases=[],summary='架空の動作検証データ',dates=[],display_date=({'mode':'anchored','anchor_year':year,'anchor_reason':'テスト'} if year is not None else {'mode':'unknown'}),claim_ids=(['claim_origin'] if id=='node_old' else []),adoption_status=status,adoption_reason='説明用',provenance=p)
    edge=dict(id='edge_link',schema_version='0.5',source_id='node_old',target_id='node_new',relation_type='hypothesis',direction_meaning='架空の仮説接続',label='仮説',claim_ids=[],adoption_status='adopted',adoption_reason='説明用',provenance=p)
    return {'nodes':[node('node_old','古い概念',-100),node('node_new','新しい概念',1950),node('node_unknown','年代不明',None),node('node_pending','調査候補',2000,'pending')],'edges':[edge],'claims':[cl],'evidence':[ev],'merge_history':[],'assets':[]}


def test_schema_and_adoption():
    d=sample();v=validate(d);assert len(v['adopted_node_ids'])==3 and len(v['adopted_edge_ids'])==1
    d['edges'][0]['target_id']='node_pending'
    with pytest.raises(ValidationError):validate(d)


def test_five_states():
    for state in ['verified','favored','contested','hypothetical','unverified']:
        d=sample();d['claims'][0]['assessment']=state
        assert validate(d)


def test_draw_all_and_time_direction(tmp_path):
    d=sample();s=layout(d,'run','架空の動作テスト')
    assert {v['node_id'] for v in s['nodes']}=={'node_old','node_new','node_unknown'}
    assert s['time_axis']['anchors'][0][1]<s['time_axis']['anchors'][1][1]
    assert s['time_axis']['anchors'][0][0]<s['time_axis']['anchors'][1][0]
    out=emit(s,tmp_path)
    assert (tmp_path/'map.svg').exists() and (tmp_path/'map.html').exists()
    if out['primary_format']=='pdf':
        assert (tmp_path/'map.pdf').read_bytes().startswith(b'%PDF')
    else:assert out['pdf_fallback_reason']
    assert 'data-node-id="node_old"' in (tmp_path/'map.svg').read_text()
    assert 'data-edge-id="edge_link"' in (tmp_path/'map.svg').read_text()


def test_pdf_fallback(tmp_path):
    result=emit(layout(sample()),tmp_path,force_pdf_error=True)
    assert result['primary_format']=='svg'
    assert not (tmp_path/'map.pdf').exists()


def test_generation_recovery(tmp_path):
    base=create_run(tmp_path,'架空例')
    gen=commit_generation(base,sample())
    assert gen=='g000001'
    assert latest_good(base)==gen
    task=add_task(base,'背景調査')
    update_task(base,task['task_id'],'running')
    progress=resume(base)
    assert progress['tasks'][0]['status']=='retryable'
    assert progress['active_generation']==gen
    (base/'snapshots'/gen/'nodes.jsonl').write_text('broken\n')
    assert latest_good(base)=='g000000'
    assert resume(base)['active_generation']=='g000000'


def test_concurrent_task_add(tmp_path):
    base=create_run(tmp_path,'並行タスクテスト')
    def add(i):
        return add_task(base,f'並行タスク{i}')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(add,range(8)))
    progress=read_progress(base)
    assert len(results)==8
    assert len(progress['tasks'])==8
    assert len(progress['frontier_task_ids'])==8

def test_atomic_generation_invalid_data(tmp_path):
    base=create_run(tmp_path,'sample');d=sample();d['edges'][0]['source_id']='node_missing'
    with pytest.raises(ValidationError):commit_generation(base,d)
    assert read_progress(base)['active_generation']=='g000000'


def test_repair_approval_and_rollback(tmp_path):
    root=tmp_path; target=root/'file.txt';target.write_text('user uncommitted change')
    prop=proposal(root,{'file.txt':'changed by agent'},'test rollback',[['python','-c','import sys;sys.exit(1)']])
    with pytest.raises(RepairError):apply(root,prop,approval=False)
    assert target.read_text()=='user uncommitted change'
    result=apply(root,prop,approval=True)
    assert result['status']=='failed' and result['rollback']=='restored'
    assert target.read_text()=='user uncommitted change'


def test_repair_success_and_cas(tmp_path):
    target=tmp_path/'file.txt';target.write_text('orig')
    p=proposal(tmp_path,{'file.txt':'new'},'test', [['python','-c','print("passed")']])
    target.write_text('unexpected modification')
    with pytest.raises(RepairError):apply(tmp_path,p,approval=True)
    assert target.read_text()=='unexpected modification'


def test_completion_status():
    assert classify(True,True,True,True,'pass')=='complete'
    assert classify(True,True,True,True,'unavailable')=='complete_limited_review'
    assert classify(True,True,True,False,'pass')=='incomplete'
    assert classify(False,True,True,True,'pass')=='incomplete'
    assert classify(True,True,True,True,'not_run')=='incomplete'

def test_reversible_entity_merge():
    from truth_seeker.entities import merge_nodes,split_nodes
    d=sample();d['nodes'][1]['type']='concept';merged,mid=merge_nodes(d,'node_new','node_old','Identical referent confirmed by matching identifiers')
    assert len(merged['nodes'])==3
    assert merged['edges'][0]['target_id']=='node_old'
    restored=split_nodes(merged,mid)
    assert validate(restored)
    assert restored['edges'][0]['target_id']=='node_new'
    assert {n['id'] for n in restored['nodes']}=={n['id'] for n in d['nodes']}

def test_render_same_year_dense_no_overlap():
    d=sample()
    for i in range(12):
        n=dict(d['nodes'][0]);n['id']=f'node_dense_{i}';n['label']=f'同年項目{i}';n['claim_ids']=[]
        d['nodes'].append(n)
    s=layout(d)
    rectangles=[n['bbox'] for n in s['nodes']]
    for i,(x,y,w,h) in enumerate(rectangles):
        for xx,yy,ww,hh in rectangles[i+1:]:
            assert not (x<xx+ww and xx<x+w and y<yy+hh and yy<y+h)
    assert check_scene(s)['status']=='pass'


def test_git_protection(tmp_path):
    from truth_seeker.repair import staged_check
    repo=tmp_path
    subprocess.run(['git','init','-q'],cwd=repo,check=True)
    (repo/'secret.txt').write_text('api_key=very_secret_value')
    subprocess.run(['git','add','secret.txt'],cwd=repo,check=True)
    result=staged_check(repo)
    assert not result['safe'] and 'secret.txt' in str(result['problems'])


def test_sanitize_folder_theme():
    assert sanitize_folder_theme('囲碁') == '囲碁'
    assert sanitize_folder_theme('囲碁の歴史: 起源から現代まで？') == '囲碁の歴史_起源から現代まで'
    assert sanitize_folder_theme('a' * 50) == 'a' * 30
    assert sanitize_folder_theme('Windows\\Linux/Forbidden:*?"<>|Chars') == 'Windows_Linux_Forbidden_Chars'
    assert sanitize_folder_theme('   ') == 'topic'
    assert sanitize_folder_theme('???:::***') == 'topic'
    # 末尾の区切り文字トリム
    assert sanitize_folder_theme('テスト_テーマ_') == 'テスト_テーマ'


def test_run_folder_naming_and_manifest(tmp_path):
    long_query = '囲碁の歴史と発展: 古代中国の起源から現代AIの登場までを徹底調査する！'
    base = create_run(tmp_path, long_query)
    folder_name = base.name
    
    # 命名規則: YYYYMMDD_HHMM_テーマ_ID
    parts = folder_name.split('_')
    assert len(parts) >= 4, f"unexpected folder name structure: {folder_name}"
    # YYYYMMDD
    assert len(parts[0]) == 8 and parts[0].isdigit()
    # HHMM
    assert len(parts[1]) == 4 and parts[1].isdigit()
    # ID: 6 hex
    suffix_id = parts[-1]
    assert len(suffix_id) == 6
    # テーマ部分
    theme_part = '_'.join(parts[2:-1])
    assert '囲碁' in theme_part
    assert len(theme_part) <= 30
    # 禁止文字が除外されていること
    assert ':' not in folder_name and '!' not in folder_name and '？' not in folder_name

    # manifest.json の検証
    manifest = read_manifest(base)
    assert manifest is not None
    assert manifest['query'] == long_query  # 全文が保持されること
    assert manifest['folder_name'] == folder_name
    assert 'created_at' in manifest and 'updated_at' in manifest
    assert manifest['run_id'].startswith('run_')  # 独立した内部ID
    
    # progress.json との整合性
    progress = read_progress(base)
    assert progress['run_id'] == manifest['run_id']
    assert progress['query'] == long_query

    # タスク追加や更新でフォルダ名は不変、manifest.json は更新される
    orig_name = base.name
    t1 = add_task(base, '起源の深掘り')
    assert base.name == orig_name
    m_after_task = read_manifest(base)
    assert m_after_task['updated_at'] >= manifest['updated_at']

    # 再開してもフォルダ名やIDは保持される
    res = resume(base)
    assert res['run_id'] == manifest['run_id']
    assert base.name == orig_name


def test_legacy_run_folder_compatibility(tmp_path):
    # 既存の run_xxx 形式フォルダを直接指定して作成・利用できること
    legacy_base = create_run(tmp_path, 'レガシー調査', run_id='run_old12345678', folder_name='run_old12345678')
    assert legacy_base.name == 'run_old12345678'
    assert read_progress(legacy_base)['run_id'] == 'run_old12345678'
    
    # タスク追加や世代コミットも正常に動くこと
    task = add_task(legacy_base, 'レガシータスク')
    assert task['run_id'] == 'run_old12345678'
    gen = commit_generation(legacy_base, sample('run_old12345678'))
    assert gen == 'g000001'


if __name__ == '__main__':
    import tempfile, inspect
    passed = 0
    failed = 0
    module = sys.modules[__name__]
    for name, func in inspect.getmembers(module, inspect.isfunction):
        if name.startswith('test_'):
            sig = inspect.signature(func)
            try:
                if 'tmp_path' in sig.parameters:
                    with tempfile.TemporaryDirectory() as td:
                        func(Path(td))
                else:
                    func()
                print(f"PASS: {name}")
                passed += 1
            except Exception as e:
                print(f"FAIL: {name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
