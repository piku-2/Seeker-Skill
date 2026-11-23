"""Evidence-aware, auditable manual entity resolution; never merge just by name."""
from copy import deepcopy
from uuid import uuid4
from .model import validate,ValidationError,VERSION

def merge_nodes(data,obsolete_id,canonical_id,reason,generation='staging'):
    """Merge only after the agent has evidence-based identity confirmation.

    Returns a new staged graph, retaining detailed prior records in merge_history.
    Canonical ID survives; edges and claim subject references are redirected.
    This is an operation on a staged graph, never the active snapshot itself.
    """
    if not reason or len(reason.strip())<8:raise ValidationError('merge requires an explicit evidence-based rationale')
    work=deepcopy(data);validate(work)
    nodes={n['id']:n for n in work['nodes']}
    if obsolete_id==canonical_id or obsolete_id not in nodes or canonical_id not in nodes:raise ValidationError('invalid merge IDs')
    old=nodes[obsolete_id];keep=nodes[canonical_id]
    if old['type']!=keep['type']:raise ValidationError('entity types differ; cannot auto merge')
    rewired_edges=[];rewired_claims=[]
    for edge in work['edges']:
        for key in ('source_id','target_id'):
            if edge[key]==obsolete_id:
                rewired_edges.append({'id':edge['id'],'field':key,'before':obsolete_id,'after':canonical_id})
                edge[key]=canonical_id
    for claim in work['claims']:
        for i,ref in enumerate(claim['subject_refs']):
            if ref['kind']=='node' and ref['id']==obsolete_id:
                rewired_claims.append({'id':claim['id'],'index':i,'before':obsolete_id,'after':canonical_id})
                ref['id']=canonical_id
    orig_keep=deepcopy(keep)
    keep['aliases']=list(dict.fromkeys([*keep['aliases'],old['label'],*old['aliases']]))
    keep['dates'].extend(x for x in old['dates'] if x not in keep['dates'])
    keep['claim_ids']=list(dict.fromkeys([*keep['claim_ids'],*old['claim_ids']]))
    work['nodes']=[n for n in work['nodes'] if n['id']!=obsolete_id]
    rec={'id':'merge_'+uuid4().hex,'schema_version':VERSION,'from_id':obsolete_id,'to_id':canonical_id,'reason':reason,'generation':generation,
         'old_node':old,'original_canonical_node':orig_keep,'rewired_edges':rewired_edges,'rewired_claims':rewired_claims}
    work['merge_history'].append(rec);validate(work)
    return work,rec['id']

def split_nodes(data,merge_id):
    """Reverse a merge conservatively; fail instead of overwriting subsequent edits."""
    work=deepcopy(data);records=[m for m in work['merge_history'] if m['id']==merge_id]
    if len(records)!=1:raise ValidationError('merge record not found')
    m=records[0];nodes={n['id']:n for n in work['nodes']}
    if m['from_id'] in nodes or m['to_id'] not in nodes:raise ValidationError('unexpected current graph')
    current=nodes[m['to_id']];original=m['original_canonical_node'];old=m['old_node']
    expected_aliases=list(dict.fromkeys([*original['aliases'],old['label'],*old['aliases']]))
    expected_dates=[*original['dates'],*(x for x in old['dates'] if x not in original['dates'])]
    expected_claim_ids=list(dict.fromkeys([*original['claim_ids'],*old['claim_ids']]))
    # If canonical node has since changed, refuse destructive reversal and let agent reassign evidence.
    expected=deepcopy(original);expected.update(aliases=expected_aliases,dates=expected_dates,claim_ids=expected_claim_ids)
    if current!=expected:raise ValidationError('canonical node edited since merge; manual reattribution needed')
    current.clear();current.update(deepcopy(original));work['nodes'].append(old)
    edges={e['id']:e for e in work['edges']};claims={c['id']:c for c in work['claims']}
    for rewrite in m['rewired_edges']:
        edge=edges.get(rewrite['id']);key=rewrite['field']
        if edge is None or edge[key]!=rewrite['after']:raise ValidationError('edge changed since merge; manual reattribution needed')
        edge[key]=rewrite['before']
    for rewrite in m['rewired_claims']:
        claim=claims.get(rewrite['id']);idx=rewrite['index']
        if claim is None or idx>=len(claim['subject_refs']) or claim['subject_refs'][idx]['id']!=rewrite['after']:raise ValidationError('claim changed since merge; manual reattribution needed')
        claim['subject_refs'][idx]['id']=rewrite['before']
    work['merge_history']=[x for x in work['merge_history'] if x['id']!=merge_id]
    validate(work);return work
