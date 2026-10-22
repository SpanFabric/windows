#!/usr/bin/env python3
"""Manual Verification Bridge v1 validator. Standard-library only."""
from __future__ import annotations
import argparse, hashlib, json, pathlib, re, subprocess, sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / '.steward' / 'verification-policy.yaml'
STATE_PATH = ROOT / 'verification' / 'state.yaml'
EXACT_EXCLUDED = {'verification/state.yaml'}
PREFIX_EXCLUDED = ('verification/reports/',)
RISK_ORDER = {'R0':0,'R1':1,'R2':2,'R3':3,'R4':4}
CONFLICT_MARKERS = (b'<<<<<<< ', b'=======', b'>>>>>>> ')
EXPECTED_EVIDENCE_MATRIX = {
    'BUILDER': {'role': 'BUILDER', 'types': ('BUILDER_REPORT', 'REMEDIATION'), 'authority': 'MANUAL_AUTHORITY'},
    'CI': {'role': 'CI', 'types': ('CI_RESULT',), 'authority': 'MANUAL_AUTHORITY'},
    'FRESH_BREAKER': {'role': 'FRESH_BREAKER', 'types': ('BREAKER_REPORT', 'BREAKER_VERDICT'), 'authority': 'MANUAL_AUTHORITY'},
    'SPECIALIST': {'role': 'SPECIALIST', 'types': ('SPECIALIST_VERDICT',), 'authority': 'MANUAL_AUTHORITY'},
    'OWNER': {'role': 'OWNER', 'types': ('ACCEPTANCE',), 'authority': 'MANUAL_AUTHORITY'},
    'MERGE_HOSTING_GATE': {'role': 'MERGE_HOSTING_GATE', 'types': ('MERGE_STATE',), 'authority': 'MANUAL_AUTHORITY'},
    'POST_MERGE_WORKFLOW': {'role': 'POST_MERGE_WORKFLOW', 'types': ('POST_MERGE_VERIFICATION',), 'authority': 'MANUAL_AUTHORITY'},
}
MERGE_SHA_FIELD = 'resulting_merge_commit_sha'
POST_MERGE_SUCCESS_VERDICTS = {'PASS', 'POST_MERGE_VERIFIED'}
POST_MERGE_FAILURE_VERDICTS = {'FAIL', 'FAILED', 'ERROR', 'MERGE_VERIFICATION_FAILED'}
HISTORY_CONTRACT = {
    'model': 'durable-git-dag-v1',
    'policy_versions': 'per-commit-snapshot',
    'merges': 'ancestor-parent-snapshot',
    'live_state': 'head-successor',
}
TRANSITION_AUTHORITIES = {
    ('PLANNED', 'BUILDING'): 'BUILDER',
    ('BUILDING', 'INDEPENDENT_REVIEW_PENDING'): 'BUILDER',
    ('INDEPENDENT_REVIEW_PENDING', 'BUILDING'): 'BUILDER',
    ('INDEPENDENT_REVIEW_PENDING', 'BREAKER_FAILED'): 'FRESH_BREAKER',
    ('INDEPENDENT_REVIEW_PENDING', 'READY_FOR_OWNER_ACCEPTANCE'): 'FRESH_BREAKER',
    ('BREAKER_FAILED', 'BUILDING'): 'BUILDER',
    ('READY_FOR_OWNER_ACCEPTANCE', 'BUILDING'): 'BUILDER',
    ('READY_FOR_OWNER_ACCEPTANCE', 'ACCEPTED'): 'OWNER',
    ('ACCEPTED', 'BUILDING'): 'BUILDER',
    ('ACCEPTED', 'MERGED'): 'MERGE_HOSTING_GATE',
    ('MERGED', 'POST_MERGE_VERIFIED'): 'POST_MERGE_WORKFLOW',
    ('MERGED', 'MERGE_VERIFICATION_FAILED'): 'POST_MERGE_WORKFLOW',
    ('MERGE_VERIFICATION_FAILED', 'BUILDING'): 'BUILDER',
}

class VerificationError(Exception):
    pass

def set_repository_root(repo_root: str|None) -> None:
    """Bind this validator to an explicit repository when run from a trusted temp copy."""
    if repo_root is None:
        return
    candidate=pathlib.Path(repo_root).expanduser()
    try:
        candidate=candidate.resolve(strict=True)
    except OSError as e:
        raise VerificationError(f'Unable to resolve --repo-root {repo_root!r}: {e}') from e
    if not candidate.is_dir():
        raise VerificationError(f'--repo-root is not a directory: {candidate}')
    global ROOT, POLICY_PATH, STATE_PATH
    ROOT=candidate
    POLICY_PATH=ROOT / '.steward' / 'verification-policy.yaml'
    STATE_PATH=ROOT / 'verification' / 'state.yaml'

def run_git(*args: str, check: bool=True) -> subprocess.CompletedProcess[bytes]:
    # The trusted runner may execute this copy as a sandbox identity distinct
    # from the checkout owner. Trust only the explicit repository root, never
    # a wildcard, so Git can still inspect its own index and object database.
    p=subprocess.run(['git','--no-replace-objects','-c',f'safe.directory={ROOT}',*args], cwd=ROOT, capture_output=True)
    if check and p.returncode != 0:
        raise VerificationError(f"git {' '.join(args)} failed: {p.stderr.decode('utf-8','replace').strip()}")
    return p

def load_json_yaml(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=history_json_pairs)
    except Exception as e:
        raise VerificationError(f'{path.relative_to(ROOT)} must be JSON-compatible YAML: {e}') from e

def canonical_git_path(path_b: bytes) -> str:
    """Decode one Git index path without applying host-path normalization."""
    if b'\\' in path_b:
        raise VerificationError(f'Non-canonical Git path contains literal backslash: {path_b!r}')
    try:
        path=path_b.decode('utf-8')
    except UnicodeDecodeError as e:
        raise VerificationError(f'Git path is not valid UTF-8: {path_b!r}') from e
    parts=path.split('/')
    if not path or path.startswith('/') or any(part in {'', '.', '..'} for part in parts):
        raise VerificationError(f'Non-canonical Git path: {path!r}')
    return path

def is_excluded(path: str) -> bool:
    # Git paths are canonical slash-separated tree names. This deliberately
    # does not translate a literal backslash, which is a different Git path.
    return path in EXACT_EXCLUDED or any(path.startswith(prefix) for prefix in PREFIX_EXCLUDED)

def tracked_entries() -> list[tuple[str,str,str]]:
    raw=run_git('ls-files','-s','-z').stdout
    entries=[]
    seen={}
    for record in raw.split(b'\0'):
        if not record: continue
        try:
            meta, path_b = record.split(b'\t',1)
            mode_b, oid_b, stage_b = meta.split(b' ',2)
        except ValueError as e:
            raise VerificationError('Unable to parse git ls-files -s output') from e
        path=canonical_git_path(path_b)
        stage=stage_b.decode('ascii')
        if stage != '0':
            raise VerificationError(f'Unmerged index entry detected for {path} (stage {stage})')
        if path in seen:
            raise VerificationError(f'Duplicate tracked path in index: {path}')
        seen[path]=True
        entries.append((path,mode_b.decode('ascii'),oid_b.decode('ascii')))
    return sorted(entries, key=lambda x: x[0].encode('utf-8'))

def content_for(path: str, mode: str, oid: str) -> tuple[str,bytes]:
    """Return the indexed Git object, never a host-normalized path lookup."""
    if mode == '120000':
        return 'symlink', run_git('cat-file','blob',oid).stdout
    if mode == '160000':
        return 'gitlink', oid.encode('ascii')
    return 'regular', run_git('cat-file','blob',oid).stdout

def compute_review_subject_digest() -> tuple[str,list[str]]:
    h=hashlib.sha256(); material=[]
    for path,mode,oid in tracked_entries():
        if is_excluded(path): continue
        kind,data=content_for(path,mode,oid)
        material.append(path)
        for label,value in (
            (b'path',path.encode('utf-8','surrogateescape')),
            (b'mode',mode.encode('ascii')),
            (b'index_oid',oid.encode('ascii')),
            (b'type',kind.encode('ascii')),
            (b'length',str(len(data)).encode('ascii')),
            (b'content',data),
        ):
            h.update(label+b'\0'+value+b'\0')
    return h.hexdigest(),material

def unstaged_material_paths() -> list[str]:
    """Reject a working tree that differs materially from its reviewed index."""
    p=run_git('diff','--no-ext-diff','--name-only','-z','--')
    paths=[]
    for path_b in p.stdout.split(b'\0'):
        if not path_b: continue
        path=canonical_git_path(path_b)
        if not is_excluded(path): paths.append(path)
    return paths

def conflict_marker_paths(material: list[str]) -> list[str]:
    bad=[]
    material_set=set(material)
    for path,mode,oid in tracked_entries():
        if path not in material_set or mode in {'120000','160000'}: continue
        data=content_for(path,mode,oid)[1]
        if b'\0' in data: continue
        lines=data.splitlines()
        if any(any(line.startswith(m) for m in CONFLICT_MARKERS) for line in lines):
            bad.append(path)
    return bad

def current_head() -> str|None:
    p=run_git('rev-parse','HEAD',check=False)
    return p.stdout.decode().strip() if p.returncode==0 else None

def resolve_commit(sha: str) -> str|None:
    if not isinstance(sha,str) or not sha:
        return None
    p=run_git('rev-parse','--verify','--quiet',f'{sha}^{{commit}}',check=False)
    return p.stdout.decode('ascii','replace').strip() if p.returncode==0 else None

def commit_exists(sha: str) -> bool:
    return resolve_commit(sha) is not None

def repository_is_shallow() -> bool:
    p=run_git('rev-parse','--is-shallow-repository',check=False)
    if p.returncode:
        raise VerificationError('Unable to determine whether repository history is shallow')
    return p.stdout.decode('ascii','replace').strip().lower() == 'true'

def is_initial_commit() -> bool:
    """Return whether HEAD is the one parentless repository bootstrap commit."""
    head=current_head()
    if not head:
        return False
    parents=run_git('rev-list','--parents','-n','1',head,check=False)
    if parents.returncode:
        return False
    fields=parents.stdout.decode('ascii','replace').strip().split()
    return fields == [head]

def allowed_initial_baseline_self_evidence(policy: dict[str,Any], state: dict[str,Any], evidence: dict[str,Any]) -> bool:
    """Permit only the initial Builder report to bind its otherwise self-referential commit."""
    bootstrap=policy.get('initial_baseline_bootstrap',{})
    transition=state.get('transition') or {}
    expected=bootstrap.get('permitted_transition',{})
    common = (
        bootstrap.get('enabled') is True
        and evidence.get('commit_sha') == bootstrap.get('self_commit_token')
        and evidence.get('producer') == 'BUILDER'
        and evidence.get('role') == bootstrap.get('permitted_role') == 'BUILDER'
        and evidence.get('type') == bootstrap.get('permitted_type') == 'BUILDER_REPORT'
    )
    if not common:
        return False
    # Inactive bootstrap evidence is historical metadata. It cannot satisfy
    # any current-state role requirement and may remain readable after a later
    # material change has invalidated it.
    if not bool(evidence.get('active',True)):
        return True
    return (
        state.get('status') == bootstrap.get('permitted_status') == 'INDEPENDENT_REVIEW_PENDING'
        and transition.get('from') == expected.get('from') == 'BUILDING'
        and transition.get('to') == expected.get('to') == 'INDEPENDENT_REVIEW_PENDING'
        and transition.get('authority') == expected.get('authority') == 'BUILDER'
        and is_initial_commit()
    )

def allowed_initial_baseline_null_base(policy: dict[str,Any], state: dict[str,Any]) -> bool:
    bootstrap=policy.get('initial_baseline_bootstrap',{})
    expected=bootstrap.get('permitted_transition',{})
    transition=state.get('transition') or {}
    return (
        bootstrap.get('enabled') is True
        and state.get('status') == bootstrap.get('permitted_status') == 'INDEPENDENT_REVIEW_PENDING'
        and state.get('review_subject_commit') == bootstrap.get('self_commit_token')
        and transition.get('from') == expected.get('from') == 'BUILDING'
        and transition.get('to') == expected.get('to') == 'INDEPENDENT_REVIEW_PENDING'
        and transition.get('authority') == expected.get('authority') == 'BUILDER'
        and is_initial_commit()
    )

def changed_paths(base: str|None, policy: dict[str,Any], state: dict[str,Any]) -> list[str]:
    if base is None:
        # This is intentionally distinct from an explicit CLI value. main()
        # permits it only for non-authoritative diagnostic output.
        return []
    if not isinstance(base,str):
        raise VerificationError('Supplied base SHA must be a string')
    if not base.strip():
        raise VerificationError('Supplied base SHA must not be empty or whitespace')
    if base != base.strip():
        raise VerificationError('Supplied base SHA must not contain surrounding whitespace')
    if base == '0'*40:
        if allowed_initial_baseline_null_base(policy,state): return []
        raise VerificationError('All-null base is permitted only for the active root-bootstrap contract')
    if not re.fullmatch(r'[0-9a-f]{40}',base):
        raise VerificationError('Supplied base SHA must be a canonical lowercase 40-hex commit SHA')
    if repository_is_shallow():
        raise VerificationError('Cannot classify risk from a shallow repository history')
    resolved=resolve_commit(base)
    if not resolved:
        raise VerificationError(f'Supplied base SHA does not resolve to a commit: {base}')
    if base != resolved:
        raise VerificationError(f'Supplied base SHA must be the exact canonical commit SHA: {base}')
    ancestor=run_git('merge-base','--is-ancestor',resolved,'HEAD',check=False)
    if ancestor.returncode != 0:
        raise VerificationError(f'Supplied base SHA is not an available ancestor of HEAD: {base}')
    p=run_git('diff','--name-only','-z',resolved,'--')
    return [canonical_git_path(x) for x in p.stdout.split(b'\0') if x]

def deterministic_risk_floor(policy: dict[str,Any], state: dict[str,Any], base: str|None) -> str:
    paths=changed_paths(base,policy,state)
    if not paths: return 'R0'
    floor='R0'
    for path in paths:
        candidate='R0'
        lower=path.lower()
        # Passive prose only remains R0 unless a stronger path rule matches.
        if pathlib.PurePosixPath(path).suffix.lower() not in {'.md','.txt','.rst','.adoc'}:
            candidate='R1'
        for rule in policy.get('risk_floor_path_rules',[]):
            if any(pattern.lower() in lower for pattern in rule.get('patterns',[])):
                if RISK_ORDER[rule['class']] > RISK_ORDER[candidate]: candidate=rule['class']
        if RISK_ORDER[candidate] > RISK_ORDER[floor]: floor=candidate
    return floor

def closed_evidence_matrix(policy: dict[str,Any]) -> dict[str,dict[str,Any]]:
    matrix=policy.get('evidence_authorization')
    if not isinstance(matrix,dict):
        raise VerificationError('Policy must define evidence_authorization as a closed mapping')
    if set(matrix) != set(EXPECTED_EVIDENCE_MATRIX):
        raise VerificationError('Policy evidence_authorization producers differ from the fixed bridge matrix')
    for producer,expected in EXPECTED_EVIDENCE_MATRIX.items():
        entry=matrix.get(producer)
        if not isinstance(entry,dict):
            raise VerificationError(f'Policy evidence_authorization entry for {producer} is invalid')
        if entry.get('role') != expected['role']:
            raise VerificationError(f'Policy evidence_authorization role drift for {producer}')
        if tuple(entry.get('types',())) != expected['types']:
            raise VerificationError(f'Policy evidence_authorization type drift for {producer}')
        if entry.get('authority') != expected['authority']:
            raise VerificationError(f'Policy evidence_authorization authority drift for {producer}')
    return matrix

def append_matrix_errors(errors: list[str], evidence: dict[str,Any], matrix: dict[str,dict[str,Any]]) -> None:
    evidence_id=evidence.get('id','<unknown>')
    producer=evidence.get('producer')
    role=evidence.get('role')
    evidence_type=evidence.get('type')
    authority=evidence.get('authority')
    expected=matrix.get(producer)
    if expected is None:
        errors.append(f'Evidence {evidence_id} has unknown producer {producer!r}')
        return
    if role != expected['role']:
        errors.append(f'Evidence {evidence_id} role {role!r} does not match producer {producer!r}')
    if evidence_type not in expected['types']:
        errors.append(f'Evidence {evidence_id} type {evidence_type!r} is not authorized for producer/role {producer!r}/{role!r}')
    if authority != expected['authority']:
        errors.append(f'Evidence {evidence_id} authority {authority!r} is not authorized for producer/role {producer!r}/{role!r}')

def canonical_merge_sha(state: dict[str,Any], errors: list[str]) -> str|None:
    merge_sha=state.get(MERGE_SHA_FIELD)
    resolved=resolve_commit(merge_sha)
    if not resolved:
        errors.append(f'{MERGE_SHA_FIELD} must name an existing resulting merge commit')
        return None
    if merge_sha != resolved:
        errors.append(f'{MERGE_SHA_FIELD} must be the exact canonical resulting merge commit SHA')
        return None
    return merge_sha

def matching_active_evidence(active_current: list[dict[str,Any]], producer: str, evidence_type: str, merge_sha: str) -> list[dict[str,Any]]:
    return [
        ev for ev in active_current
        if ev.get('producer') == producer
        and ev.get('role') == producer
        and ev.get('type') == evidence_type
        and ev.get('commit_sha') == merge_sha
        and ev.get('merge_commit_sha') == merge_sha
    ]

def validate_merge_boundary(status: str, state: dict[str,Any], active_current: list[dict[str,Any]], errors: list[str]) -> None:
    if status not in {'MERGED','POST_MERGE_VERIFIED','MERGE_VERIFICATION_FAILED'}:
        return
    merge_sha=canonical_merge_sha(state,errors)
    if not merge_sha:
        return
    merge_evidence=matching_active_evidence(active_current,'MERGE_HOSTING_GATE','MERGE_STATE',merge_sha)
    if not any(str(ev.get('verdict','')).upper() in {'PASS','MERGED'} for ev in merge_evidence):
        errors.append('Merged-state evidence must be active MERGE_HOSTING_GATE MERGE_STATE evidence bound to the resulting merge SHA')
    if status == 'MERGED':
        return
    post_evidence=matching_active_evidence(active_current,'POST_MERGE_WORKFLOW','POST_MERGE_VERIFICATION',merge_sha)
    verdicts={str(ev.get('verdict','')).upper() for ev in post_evidence}
    if status == 'POST_MERGE_VERIFIED':
        if not verdicts.intersection(POST_MERGE_SUCCESS_VERDICTS):
            errors.append('POST_MERGE_VERIFIED requires successful POST_MERGE_WORKFLOW evidence for the same resulting merge SHA')
    elif not verdicts.intersection(POST_MERGE_FAILURE_VERDICTS):
        errors.append('MERGE_VERIFICATION_FAILED requires failed POST_MERGE_WORKFLOW evidence for the same resulting merge SHA')

def history_policy(policy: dict[str,Any], where: str) -> None:
    """Versioned policy snapshots can restrict, never widen the closed role graph."""
    if not isinstance(policy,dict) or policy.get('schema_version') != '1.0':
        raise VerificationError(f'Lifecycle history: unsupported policy version at {where}')
    if 'history_validation' in policy and policy['history_validation'] != HISTORY_CONTRACT:
        raise VerificationError(f'Lifecycle history: unsupported history contract at {where}')
    known={state for edge in TRANSITION_AUTHORITIES for state in edge}
    state_names=policy.get('states')
    if not isinstance(state_names,list) or not all(isinstance(s,str) for s in state_names) or set(state_names) != known:
        raise VerificationError(f'Lifecycle history: unsupported state set at {where}')
    seen=set()
    transitions=policy.get('transitions')
    if not isinstance(transitions,list):
        raise VerificationError(f'Lifecycle history: missing transition policy at {where}')
    for rule in transitions:
        if not isinstance(rule,dict):
            raise VerificationError(f'Lifecycle history: malformed transition policy at {where}')
        if not isinstance(rule.get('from'),str) or not isinstance(rule.get('to'),str):
            raise VerificationError(f'Lifecycle history: malformed transition endpoints at {where}')
        edge=(rule.get('from'),rule.get('to'))
        if edge in seen or edge not in TRANSITION_AUTHORITIES or rule.get('authorities') != [TRANSITION_AUTHORITIES[edge]]:
            raise VerificationError(f'Lifecycle history: transition authority drift at {where}: {edge}')
        seen.add(edge)

def history_projection(state: dict[str,Any], where: str) -> tuple[Any,...]:
    if not isinstance(state,dict) or state.get('authority_mode') != 'MANUAL_AUTHORITY':
        raise VerificationError(f'Lifecycle history: malformed state at {where}')
    status=state.get('status'); previous=state.get('previous_status'); transition=state.get('transition')
    if not isinstance(status,str) or status not in {value for edge in TRANSITION_AUTHORITIES for value in edge}:
        raise VerificationError(f'Lifecycle history: unknown state at {where}')
    if transition is None:
        if status != 'PLANNED' or previous is not None:
            raise VerificationError(f'Lifecycle history: missing transition at {where}')
        return (status,None,None)
    if not isinstance(transition,dict) or set(transition) != {'from','to','authority'}:
        raise VerificationError(f'Lifecycle history: malformed transition at {where}')
    if transition['from'] != previous or transition['to'] != status:
        raise VerificationError(f'Lifecycle history: transition endpoints mismatch at {where}')
    return (status,previous,transition['from'],transition['to'],transition['authority'])

def history_step(parent: dict[str,Any], state: dict[str,Any], policy: dict[str,Any], where: str) -> None:
    current=history_projection(state,where)
    if current == history_projection(parent,where+' predecessor'):
        # Reports/digests can change without re-consuming the retained transition.
        return
    transition=state.get('transition') or {}
    if transition.get('from') != parent.get('status'):
        raise VerificationError(f'Lifecycle history: unreachable predecessor at {where}: '
                                f'{parent.get("status")} -> {state.get("previous_status")}')
    if not any(rule == {'from':transition.get('from'),'to':transition.get('to'),
                        'authorities':[transition.get('authority')]} for rule in policy['transitions']):
        raise VerificationError(f'Lifecycle history: unauthorized durable transition at {where}: {transition}')

def history_root(state: dict[str,Any], policy: dict[str,Any], where: str) -> None:
    if history_projection(state,where) == ('PLANNED',None,None):
        return
    bootstrap=policy.get('initial_baseline_bootstrap',{})
    token='INITIAL_BASELINE_BOOTSTRAP_SELF'
    expected={'from':'BUILDING','to':'INDEPENDENT_REVIEW_PENDING','authority':'BUILDER'}
    if (bootstrap.get('enabled') is True
        and bootstrap.get('self_commit_token') == token
        and bootstrap.get('permitted_role') == 'BUILDER'
        and bootstrap.get('permitted_type') == 'BUILDER_REPORT'
        and bootstrap.get('permitted_status') == 'INDEPENDENT_REVIEW_PENDING'
        and bootstrap.get('permitted_transition') == expected
        and state.get('transition') == expected
        and state.get('review_subject_commit') == token
        and any(ev.get('active',True) and ev.get('producer') == ev.get('role') == 'BUILDER'
                and ev.get('type') == 'BUILDER_REPORT' and ev.get('commit_sha') == token
                and ev.get('authority') == 'MANUAL_AUTHORITY' for ev in state.get('evidence',[]))):
        return
    raise VerificationError(f'Lifecycle history: root is neither PLANNED nor explicit root bootstrap at {where}')

def history_objects(queries: list[str]) -> list[tuple[str,str,bytes]]:
    """Batch immutable objects, avoiding per-blob process startup on native Windows."""
    p=subprocess.run(['git','--no-replace-objects','-c',f'safe.directory={ROOT}','cat-file','--batch'],
                     cwd=ROOT,input=('\n'.join(queries)+'\n').encode('ascii'),capture_output=True)
    if p.returncode:
        raise VerificationError('Lifecycle history: required objects unavailable: '+p.stderr.decode('utf-8','replace'))
    result=[]; offset=0
    for query in queries:
        end=p.stdout.find(b'\n',offset)
        fields=p.stdout[offset:end].split(); offset=end+1
        if end < 0 or len(fields)!=3 or not fields[2].isdigit():
            raise VerificationError(f'Lifecycle history: missing/invalid object {query}')
        size=int(fields[2]); data=p.stdout[offset:offset+size]; offset+=size
        if len(data)!=size or p.stdout[offset:offset+1]!=b'\n':
            raise VerificationError(f'Lifecycle history: incomplete object {query}')
        offset+=1; result.append((fields[0].decode('ascii'),fields[1].decode('ascii'),data))
    if offset != len(p.stdout):
        raise VerificationError('Lifecycle history: unexpected object batch data')
    return result

def history_tree_entry(data: bytes, name: bytes) -> tuple[str,str]|None:
    offset=0; result=None; seen=set()
    while offset < len(data):
        end=data.find(b'\0',offset)
        if end<0 or end+21>len(data):
            raise VerificationError('Lifecycle history: malformed SHA-1 tree')
        mode,entry=data[offset:end].split(b' ',1)
        if entry in seen:
            raise VerificationError('Lifecycle history: duplicate tree entry')
        seen.add(entry)
        oid=data[end+1:end+21].hex(); offset=end+21
        if entry == name:
            result=mode.decode('ascii'),oid
    return result

def history_json_pairs(pairs: list[tuple[str,Any]]) -> dict[str,Any]:
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field {key}')
        result[key]=value
    return result

def validate_lifecycle_history(policy: dict[str,Any], state: dict[str,Any]) -> None:
    """Check every reachable durable state, independent of caller-selected base.

    Policy is effective atomically at its own commit, never retroactively.
    A merge may only adopt an exact parent snapshot if every other parent is
    its ancestor. Divergent reconciliation has no implicit authority.
    """
    history_policy(policy,'live policy')
    if policy.get('history_validation') != HISTORY_CONTRACT:
        raise VerificationError('Lifecycle history: current policy must declare durable-git-dag-v1')
    if repository_is_shallow():
        raise VerificationError('Lifecycle history: shallow history cannot establish reachability')
    graft_path=run_git('rev-parse','--git-path','info/grafts').stdout.decode().strip()
    graft=pathlib.Path(graft_path)
    if not graft.is_absolute(): graft=ROOT/graft
    if graft.exists() and graft.read_bytes().strip():
        raise VerificationError('Lifecycle history: grafted history is not authoritative')
    if run_git('rev-parse','--show-object-format').stdout.strip() != b'sha1':
        raise VerificationError('Lifecycle history: unsupported Git object format')
    rows=[line.split() for line in run_git('rev-list','--topo-order','--reverse','--parents','HEAD').stdout.decode('ascii').splitlines()]
    if not rows or sum(len(row)==1 for row in rows)!=1:
        raise VerificationError('Lifecycle history: one complete root is required')
    paths=('verification/state.yaml','.steward/verification-policy.yaml','verification','.steward')
    objects=history_objects([f'{row[0]}:{path}' for row in rows for path in paths])
    validated={}; json_cache={}
    for index,row in enumerate(rows):
        sha,*parents=row
        if any(parent not in validated for parent in parents):
            raise VerificationError(f'Lifecycle history: missing parent of {sha}')
        so,po,st,pt=objects[index*4:index*4+4]
        if (so[1]!='blob' or po[1]!='blob' or st[1]!='tree' or pt[1]!='tree'
            or history_tree_entry(st[2],b'state.yaml') != ('100644',so[0])
            or history_tree_entry(pt[2],b'verification-policy.yaml') != ('100644',po[0])):
            raise VerificationError(f'Lifecycle history: state/policy must be regular Git blobs at {sha}')
        for oid,kind,data in (so,po):
            if oid not in json_cache:
                try: json_cache[oid]=json.loads(data.decode('utf-8'),object_pairs_hook=history_json_pairs)
                except (UnicodeError,ValueError) as exc:
                    raise VerificationError(f'Lifecycle history: invalid JSON snapshot at {sha}') from exc
        snapshot=json_cache[so[0]]; rules=json_cache[po[0]]
        history_policy(rules,sha); history_projection(snapshot,sha)
        if not parents:
            history_root(snapshot,rules,sha)
        elif len(parents)==1:
            history_step(validated[parents[0]][0],snapshot,rules,sha)
        elif len(parents)==2:
            selected=[parent for parent in parents
                      if validated[parent][1:] == (so[0],po[0])
                      and all(other==parent or run_git('merge-base','--is-ancestor',other,parent,check=False).returncode==0
                              for other in parents)]
            if len(selected)!=1:
                raise VerificationError(f'Lifecycle history: unsupported/ambiguous merge topology at {sha}')
        else:
            raise VerificationError(f'Lifecycle history: unsupported octopus merge topology at {sha}')
        validated[sha]=(snapshot,so[0],po[0])
    # Uncommitted excluded state is a single proposed successor, not a new root
    # or an excuse to omit committed intermediate transitions.
    if len(rows)==1 and allowed_initial_baseline_null_base(policy,state):
        history_root(state,policy,'live root bootstrap')
    else:
        history_step(validated[rows[-1][0]][0],state,policy,'live state')

def validate(base: str|None=None) -> tuple[str,list[str]]:
    unstaged=unstaged_material_paths()
    if unstaged:
        raise VerificationError('Unstaged material changes must be staged before verification: '+', '.join(unstaged))
    policy=load_json_yaml(POLICY_PATH); state=load_json_yaml(STATE_PATH)
    matrix=closed_evidence_matrix(policy)
    errors=[]
    states=set(policy.get('states',[]))
    status=state.get('status')
    if status not in states: errors.append(f'Unknown state: {status!r}')
    if state.get('authority_mode')!='MANUAL_AUTHORITY': errors.append('state.authority_mode must be MANUAL_AUTHORITY during bridge v1')

    digest,material=compute_review_subject_digest()
    markers=conflict_marker_paths(material)
    if markers: errors.append('Conflict markers in material files: '+', '.join(markers))

    transition=state.get('transition')
    previous=state.get('previous_status')
    try:
        head_state=json.loads(run_git('cat-file','blob','HEAD:verification/state.yaml').stdout.decode('utf-8'))
    except (UnicodeError,ValueError) as exc:
        raise VerificationError('Lifecycle history: invalid HEAD state') from exc
    if not isinstance(head_state,dict):
        raise VerificationError('Lifecycle history: HEAD state must be an object')
    retained=(status,previous,transition)==tuple(head_state.get(key) for key in ('status','previous_status','transition'))
    if transition:
        frm=transition.get('from'); to=transition.get('to'); authority=transition.get('authority')
        if frm != previous or to != status: errors.append('transition from/to must match previous_status/status')
        allowed=[t for t in policy.get('transitions',[]) if t.get('from')==frm and t.get('to')==to]
        # A retained event was consumed under its historical policy; history
        # validation below proves that. A new live event uses current policy.
        if not retained:
            if not allowed: errors.append(f'Invalid state transition {frm!r} -> {to!r}')
            elif authority not in set(allowed[0].get('authorities',[])): errors.append(f'Authority {authority!r} cannot perform transition {frm}->{to}')
    elif previous is not None:
        errors.append('previous_status requires a transition record')
    elif status != 'PLANNED':
        errors.append('Only initial PLANNED state may omit transition metadata')

    risk=state.get('risk',{})
    declared=risk.get('class')
    if declared not in RISK_ORDER: errors.append(f'Invalid risk class: {declared!r}')
    else:
        floor=deterministic_risk_floor(policy,state,base)
        if RISK_ORDER[declared] < RISK_ORDER[floor]: errors.append(f'Declared risk {declared} is below deterministic floor {floor}')

    evidence=state.get('evidence',[])
    active=[]
    valid_subject_commits=set()
    required=set(policy.get('evidence_required_fields',[]))
    for ev in evidence:
        missing=sorted(required-set(ev))
        if missing: errors.append(f"Evidence {ev.get('id','<unknown>')} missing fields: {', '.join(missing)}"); continue
        role=ev['role']; verdict=str(ev['verdict']).upper()
        append_matrix_errors(errors,ev,matrix)
        if ev['type'] in {'MERGE_STATE','POST_MERGE_VERIFICATION'} and not isinstance(ev.get('merge_commit_sha'),str):
            errors.append(f"Evidence {ev['id']} of type {ev['type']} requires merge_commit_sha")
        if ev['commit_sha'] == policy.get('initial_baseline_bootstrap',{}).get('self_commit_token'):
            if not allowed_initial_baseline_self_evidence(policy,state,ev):
                errors.append(f"Evidence {ev['id']} illegally uses the initial-baseline self commit token")
            else:
                valid_subject_commits.add(ev['commit_sha'])
        else:
            resolved=resolve_commit(ev['commit_sha'])
            if not resolved:
                errors.append(f"Evidence {ev['id']} references unknown commit {ev['commit_sha']}")
            elif ev['commit_sha'] != resolved:
                errors.append(f"Evidence {ev['id']} commit_sha must be an exact canonical commit SHA")
            else:
                valid_subject_commits.add(ev['commit_sha'])
        is_active=bool(ev.get('active',True))
        if is_active:
            active.append(ev)
            if ev['review_subject_digest'] != digest:
                errors.append(f"Active evidence {ev['id']} is stale: {ev['review_subject_digest']} != {digest}")
        if verdict=='PASS' and role not in {'CI','FRESH_BREAKER','SPECIALIST','OWNER','POST_MERGE_WORKFLOW','MERGE_HOSTING_GATE'}:
            errors.append(f"PASS evidence {ev['id']} is not from an authorized PASS-producing role")

    blockers=[b for b in state.get('blockers',[]) if b.get('confirmed') and b.get('status')=='OPEN']
    if blockers and status in {'READY_FOR_OWNER_ACCEPTANCE','ACCEPTED','MERGED','POST_MERGE_VERIFIED'}:
        errors.append('Confirmed open blockers forbid acceptance/merge/post-merge verified states')

    # States that assert reviewed/accepted progress require a digest and role evidence.
    if status not in {'PLANNED','BUILDING'}:
        if state.get('review_subject_digest') != digest:
            errors.append('state.review_subject_digest does not match current material tree')
        if state.get('review_subject_commit') not in valid_subject_commits:
            errors.append('state.review_subject_commit is not bound to validated evidence')
    active_current=[ev for ev in active if ev.get('review_subject_digest')==digest]
    roles={ev.get('role') for ev in active_current}
    if status=='INDEPENDENT_REVIEW_PENDING' and 'BUILDER' not in roles:
        errors.append('INDEPENDENT_REVIEW_PENDING requires active BUILDER evidence for current digest')
    if status=='BREAKER_FAILED' and 'FRESH_BREAKER' not in roles:
        errors.append('BREAKER_FAILED requires active FRESH_BREAKER evidence for current digest')
    if status=='READY_FOR_OWNER_ACCEPTANCE' and 'FRESH_BREAKER' not in roles:
        errors.append('READY_FOR_OWNER_ACCEPTANCE requires active FRESH_BREAKER evidence for current digest')
    if status in {'ACCEPTED','MERGED','POST_MERGE_VERIFIED'} and 'OWNER' not in roles:
        errors.append(f'{status} requires active OWNER acceptance evidence for current digest')
    validate_merge_boundary(status,state,active_current,errors)

    if errors: raise VerificationError('\n'.join(errors))
    validate_lifecycle_history(policy,state)
    return digest,material

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--check',action='store_true')
    ap.add_argument('--digest',action='store_true')
    ap.add_argument('--base')
    ap.add_argument('--repo-root',help='repository root when executed from an isolated trusted copy')
    args=ap.parse_args()
    try:
        set_repository_root(args.repo_root)
        if args.check and args.base is None:
            raise VerificationError('Authoritative --check requires an explicit --base SHA; use --digest only for no-base diagnostics')
        digest,material=validate(args.base)
        if args.digest or args.check:
            print(f'REVIEW_SUBJECT_SHA256={digest}')
            print(f'MATERIAL_FILE_COUNT={len(material)}')
        return 0
    except VerificationError as e:
        print(f'VERIFICATION_STATE_INVALID: {e}',file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
