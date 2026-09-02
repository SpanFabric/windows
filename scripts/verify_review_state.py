#!/usr/bin/env python3
"""Manual Verification Bridge v1 validator. Standard-library only."""
from __future__ import annotations
import argparse, fnmatch, hashlib, json, os, pathlib, re, subprocess, sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / '.steward' / 'verification-policy.yaml'
STATE_PATH = ROOT / 'verification' / 'state.yaml'
EXACT_EXCLUDED = {'verification/state.yaml'}
PREFIX_EXCLUDED = ('verification/reports/',)
RISK_ORDER = {'R0':0,'R1':1,'R2':2,'R3':3,'R4':4}
CONFLICT_MARKERS = (b'<<<<<<< ', b'=======', b'>>>>>>> ')

class VerificationError(Exception):
    pass

def run_git(*args: str, check: bool=True) -> subprocess.CompletedProcess[bytes]:
    p=subprocess.run(['git',*args], cwd=ROOT, capture_output=True)
    if check and p.returncode != 0:
        raise VerificationError(f"git {' '.join(args)} failed: {p.stderr.decode('utf-8','replace').strip()}")
    return p

def load_json_yaml(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        raise VerificationError(f'{path.relative_to(ROOT)} must be JSON-compatible YAML: {e}') from e

def is_excluded(path: str) -> bool:
    p=path.replace('\\','/')
    return p in EXACT_EXCLUDED or any(p.startswith(prefix) for prefix in PREFIX_EXCLUDED)

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
        path=path_b.decode('utf-8','surrogateescape').replace('\\','/')
        stage=stage_b.decode('ascii')
        if stage != '0':
            raise VerificationError(f'Unmerged index entry detected for {path} (stage {stage})')
        if path in seen:
            raise VerificationError(f'Duplicate tracked path in index: {path}')
        seen[path]=True
        entries.append((path,mode_b.decode('ascii'),oid_b.decode('ascii')))
    return sorted(entries, key=lambda x: x[0].encode('utf-8','surrogateescape'))

def content_for(path: str, mode: str, oid: str) -> tuple[str,bytes]:
    wp=ROOT/path
    if mode == '120000':
        if wp.is_symlink():
            return 'symlink', os.readlink(wp).encode('utf-8','surrogateescape')
        if wp.exists():
            # core.symlinks=false represents the symlink target as file text.
            return 'symlink', wp.read_bytes()
        # A tracked symlink can be present only in the index (for example in
        # Windows tests without symlink privilege). Its blob content is the
        # symlink target and remains part of the review subject.
        return 'symlink', run_git('cat-file','blob',oid).stdout
    if mode == '160000':
        return 'gitlink', oid.encode('ascii')
    if not wp.exists():
        return 'missing', b''
    if wp.is_dir():
        return 'directory-unexpected', b''
    return 'regular', wp.read_bytes()

def compute_review_subject_digest() -> tuple[str,list[str]]:
    h=hashlib.sha256(); material=[]
    for path,mode,oid in tracked_entries():
        if is_excluded(path): continue
        kind,data=content_for(path,mode,oid)
        material.append(path)
        for label,value in (
            (b'path',path.encode('utf-8','surrogateescape')),
            (b'mode',mode.encode('ascii')),
            (b'type',kind.encode('ascii')),
            (b'length',str(len(data)).encode('ascii')),
            (b'content',data),
        ):
            h.update(label+b'\0'+value+b'\0')
    return h.hexdigest(),material

def conflict_marker_paths(material: list[str]) -> list[str]:
    bad=[]
    for path in material:
        wp=ROOT/path
        if not wp.exists() or wp.is_dir() or wp.is_symlink(): continue
        data=wp.read_bytes()
        if b'\0' in data: continue
        lines=data.splitlines()
        if any(any(line.startswith(m) for m in CONFLICT_MARKERS) for line in lines):
            bad.append(path)
    return bad

def current_head() -> str|None:
    p=run_git('rev-parse','HEAD',check=False)
    return p.stdout.decode().strip() if p.returncode==0 else None

def commit_exists(sha: str) -> bool:
    return bool(sha) and run_git('cat-file','-e',f'{sha}^{{commit}}',check=False).returncode==0

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
    return (
        bootstrap.get('enabled') is True
        and evidence.get('commit_sha') == bootstrap.get('self_commit_token')
        and evidence.get('producer') == 'BUILDER'
        and evidence.get('role') == bootstrap.get('permitted_role') == 'BUILDER'
        and evidence.get('type') == bootstrap.get('permitted_type') == 'BUILDER_REPORT'
        and state.get('status') == bootstrap.get('permitted_status') == 'INDEPENDENT_REVIEW_PENDING'
        and transition.get('from') == expected.get('from') == 'BUILDING'
        and transition.get('to') == expected.get('to') == 'INDEPENDENT_REVIEW_PENDING'
        and transition.get('authority') == expected.get('authority') == 'BUILDER'
        and is_initial_commit()
    )

def changed_paths(base: str|None) -> list[str]:
    if not base: return []
    if re.fullmatch(r'0+',base): return []
    if not commit_exists(base): return []
    p=run_git('diff','--name-only','-z',base,'--')
    return [x.decode('utf-8','surrogateescape').replace('\\','/') for x in p.stdout.split(b'\0') if x]

def deterministic_risk_floor(policy: dict[str,Any], base: str|None) -> str:
    paths=changed_paths(base)
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

def validate(base: str|None=None) -> tuple[str,list[str]]:
    policy=load_json_yaml(POLICY_PATH); state=load_json_yaml(STATE_PATH)
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
    if transition:
        frm=transition.get('from'); to=transition.get('to'); authority=transition.get('authority')
        if frm != previous or to != status: errors.append('transition from/to must match previous_status/status')
        allowed=[t for t in policy.get('transitions',[]) if t.get('from')==frm and t.get('to')==to]
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
        floor=deterministic_risk_floor(policy,base)
        if RISK_ORDER[declared] < RISK_ORDER[floor]: errors.append(f'Declared risk {declared} is below deterministic floor {floor}')

    evidence=state.get('evidence',[])
    active=[]
    valid_subject_commits=set()
    required=set(policy.get('evidence_required_fields',[]))
    for ev in evidence:
        missing=sorted(required-set(ev))
        if missing: errors.append(f"Evidence {ev.get('id','<unknown>')} missing fields: {', '.join(missing)}"); continue
        role=ev['role']; producer=ev['producer']; authority=ev['authority']; verdict=str(ev['verdict']).upper()
        if producer=='BUILDER' and role in set(policy.get('builder_forbidden_roles',[])):
            errors.append(f"Builder evidence {ev['id']} illegally claims independent role {role}")
        allowed_types=set(policy.get('authorities',{}).get(role,[]))
        if ev['type'] not in allowed_types:
            errors.append(f"Evidence {ev['id']} type {ev['type']} is not authorized for role {role}")
        if role in set(policy.get('independent_roles',[])) and authority!='MANUAL_AUTHORITY':
            errors.append(f"Independent evidence {ev['id']} must explicitly use MANUAL_AUTHORITY in bridge v1")
        if ev['commit_sha'] == policy.get('initial_baseline_bootstrap',{}).get('self_commit_token'):
            if not allowed_initial_baseline_self_evidence(policy,state,ev):
                errors.append(f"Evidence {ev['id']} illegally uses the initial-baseline self commit token")
            else:
                valid_subject_commits.add(ev['commit_sha'])
        elif not commit_exists(ev['commit_sha']):
            errors.append(f"Evidence {ev['id']} references unknown commit {ev['commit_sha']}")
        else:
            valid_subject_commits.add(ev['commit_sha'])
        is_active=bool(ev.get('active',True))
        if is_active:
            active.append(ev)
            if ev['review_subject_digest'] != digest:
                errors.append(f"Active evidence {ev['id']} is stale: {ev['review_subject_digest']} != {digest}")
        if verdict=='PASS' and role not in {'CI','FRESH_BREAKER','SPECIALIST','OWNER','POST_MERGE_WORKFLOW'}:
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
    roles={ev.get('role') for ev in active if ev.get('review_subject_digest')==digest}
    if status=='INDEPENDENT_REVIEW_PENDING' and 'BUILDER' not in roles:
        errors.append('INDEPENDENT_REVIEW_PENDING requires active BUILDER evidence for current digest')
    if status=='BREAKER_FAILED' and 'FRESH_BREAKER' not in roles:
        errors.append('BREAKER_FAILED requires active FRESH_BREAKER evidence for current digest')
    if status=='READY_FOR_OWNER_ACCEPTANCE' and 'FRESH_BREAKER' not in roles:
        errors.append('READY_FOR_OWNER_ACCEPTANCE requires active FRESH_BREAKER evidence for current digest')
    if status in {'ACCEPTED','MERGED','POST_MERGE_VERIFIED'} and 'OWNER' not in roles:
        errors.append(f'{status} requires active OWNER acceptance evidence for current digest')

    if errors: raise VerificationError('\n'.join(errors))
    return digest,material

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--check',action='store_true')
    ap.add_argument('--digest',action='store_true')
    ap.add_argument('--base')
    args=ap.parse_args()
    try:
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
