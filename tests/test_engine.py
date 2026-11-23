from pathlib import Path
import json
import subprocess
import sys
import pytest
from truth_seeker.model import validate,ValidationError,provenance,FILES,dump_records,load_generation
from truth_seeker.store import create_run,commit_generation,latest_good,resume,add_task,update_task,read_progress
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
