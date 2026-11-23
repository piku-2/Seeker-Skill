"""Generation-based durable graph snapshots and resumable task state."""
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
import json, os, shutil, hashlib
from .model import FILES,load_generation,validate,dump_records,ValidationError

def atomic_json(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
        f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def utcnow(): return datetime.now(timezone.utc).isoformat()

def create_run(root,query,run_id=None):
    run_id=run_id or ('run_'+uuid4().hex[:12]); base=Path(root)/run_id
    if base.exists(): raise FileExistsError(base)
    (base/'snapshots').mkdir(parents=True)
    for d in ('staging','render','qa'): (base/d).mkdir()
    initial={'schema_version':'0.5','run_id':run_id,'query':query,'run_status':'paused','active_generation':'g000000','tasks':[],'frontier_task_ids':[],'completed_task_ids':[],'blocked_task_ids':[],'last_successful_phase':'initialized','checkpoint_at':utcnow()}
    commit_generation(base,{k:[] for k in FILES}, progress=initial)
    atomic_json(base/'manifest.json',{'run_id':run_id,'query':query,'created_at':utcnow(),'artifacts':[],'status':'paused'})
    return base

def read_progress(base): return json.loads((Path(base)/'progress.json').read_text(encoding='utf-8'))

def commit_generation(base,data,progress=None):
    base=Path(base);check=validate(data)
    old=read_progress(base) if (base/'progress.json').exists() else None
    number=(int(old['active_generation'][1:])+1 if old else 0)
    gen=f'g{number:06d}';tmp=base/'snapshots'/('_'+gen+'_'+uuid4().hex)
    tmp.mkdir(parents=True,exist_ok=False)
    for name in FILES: dump_records(tmp/(name+'.jsonl'),data.get(name,[]))
    digest={name:hashlib.sha256((tmp/(name+'.jsonl')).read_bytes()).hexdigest() for name in FILES}
    atomic_json(tmp/'index.json',{'generation':gen,'sha256':digest,'counts':check['counts']})
    validate(load_generation(tmp))
    dst=base/'snapshots'/gen; os.replace(tmp,dst)
    pr=dict(progress or old or {})
    pr['active_generation']=gen;pr['checkpoint_at']=utcnow()
    atomic_json(base/'progress.json',pr)
    return gen

def verify_generation(base,gen):
    root=Path(base)/'snapshots'/gen
    idx=json.loads((root/'index.json').read_text(encoding='utf-8'))
    for name,expected in idx['sha256'].items():
        if hashlib.sha256((root/(name+'.jsonl')).read_bytes()).hexdigest()!=expected: raise ValidationError('snapshot checksum mismatch '+name)
    return validate(load_generation(root))

def latest_good(base):
    base=Path(base)
    for d in sorted((base/'snapshots').glob('g[0-9]*'),reverse=True):
        try: verify_generation(base,d.name);return d.name
        except (ValidationError,FileNotFoundError,ValueError,KeyError): continue
    raise ValidationError('no valid snapshot')

def resume(base):
    base=Path(base); pr=read_progress(base)
    try: verify_generation(base,pr['active_generation'])
    except (ValidationError,FileNotFoundError,ValueError,KeyError): pr['active_generation']=latest_good(base)
    for t in pr.get('tasks',[]):
        if t.get('status')=='running': t['status']='retryable';t['last_error']='Interrupted; reclaimed on resume'
    pr['run_status']='paused';atomic_json(base/'progress.json',pr)
    return pr

def add_task(base,topic,priority=0,parent_task_id=None):
    pr=read_progress(base)
    if any(t['topic']==topic and t['status']!='superseded' for t in pr['tasks']): return False
    tid='task_'+uuid4().hex[:12]
    task=dict(task_id=tid,run_id=pr['run_id'],topic=topic,parent_task_id=parent_task_id,priority=priority,status='pending',attempts=0,assigned_worker=None,input_refs=[],output_refs=[],discovered_task_ids=[],last_error=None,updated_at=utcnow())
    pr['tasks'].append(task);pr['frontier_task_ids'].append(tid);atomic_json(Path(base)/'progress.json',pr)
    return task

def update_task(base,task_id,status,**extra):
    pr=read_progress(base)
    matched=[t for t in pr['tasks'] if t['task_id']==task_id]
    if len(matched)!=1: raise ValueError('unknown task')
    t=matched[0]; t.update(extra);t['status']=status;t['updated_at']=utcnow()
    pr['frontier_task_ids']=[x for x in pr['frontier_task_ids'] if x!=task_id]
    pr['completed_task_ids']=[x for x in pr['completed_task_ids'] if x!=task_id]
    pr['blocked_task_ids']=[x for x in pr['blocked_task_ids'] if x!=task_id]
    if status in ('pending','retryable'): pr['frontier_task_ids'].append(task_id)
    if status=='completed': pr['completed_task_ids'].append(task_id)
    if status=='blocked': pr['blocked_task_ids'].append(task_id)
    atomic_json(Path(base)/'progress.json',pr)
    return t
