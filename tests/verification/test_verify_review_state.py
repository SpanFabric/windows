#!/usr/bin/env python3
import importlib.util, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

HERE=pathlib.Path(__file__).resolve()
REPO=HERE.parents[2]
SCRIPT=REPO/'scripts/verify_review_state.py'
spec=importlib.util.spec_from_file_location('verify_review_state',SCRIPT)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def git(root,*args,check=True,input=None):
    p=subprocess.run(['git',*args],cwd=root,input=input,capture_output=True)
    if check and p.returncode: raise RuntimeError(p.stderr.decode('utf-8','replace'))
    return p

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='spangpu-verify-')
        self.root=pathlib.Path(self.tmp.name)
        git(self.root,'init','-q')
        git(self.root,'config','user.email','test@example.invalid')
        git(self.root,'config','user.name','Bridge Test')
        for rel in ['.steward','verification/reports','scripts','tests/verification','.github/workflows','src','docs']:
            (self.root/rel).mkdir(parents=True,exist_ok=True)
        # Copy bridge controls from the repository under test.
        for rel in ['.steward/verification-policy.yaml','scripts/verify_review_state.py','.github/workflows/verification-gate.yml']:
            dst=self.root/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(REPO/rel,dst)
        state=json.loads((REPO/'verification/state.yaml').read_text(encoding='utf-8'))
        state.update({'repository':'fixture','status':'PLANNED','previous_status':None,'transition':None,'review_subject_digest':'','review_subject_commit':'','evidence':[],'blockers':[]})
        (self.root/'verification/state.yaml').write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
        (self.root/'verification/reports/README.md').write_text('report metadata\n',encoding='utf-8')
        (self.root/'src/code.py').write_text('VALUE = 1\n',encoding='utf-8')
        (self.root/'docs/requirement.md').write_text('# Requirement\n',encoding='utf-8')
        (self.root/'tests/verification/test_dummy.py').write_text('x=1\n',encoding='utf-8')
        git(self.root,'add','-A'); git(self.root,'commit','-qm','baseline')
        self._load()

    def tearDown(self): self.tmp.cleanup()

    def _load(self):
        # Import a fresh module bound to fixture root.
        spec=importlib.util.spec_from_file_location('fixture_verify',self.root/'scripts/verify_review_state.py')
        self.v=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.v)

    def digest(self): return self.v.compute_review_subject_digest()[0]

    def write_state(self,**kw):
        p=self.root/'verification/state.yaml'; d=json.loads(p.read_text(encoding='utf-8')); d.update(kw); p.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8')

    def builder_evidence(self,digest,role='BUILDER',producer='BUILDER',etype='BUILDER_REPORT',verdict='PASS'):
        sha=git(self.root,'rev-parse','HEAD').stdout.decode().strip()
        return {'id':'EV-1','type':etype,'producer':producer,'role':role,'authority':'MANUAL_AUTHORITY' if role in {'FRESH_BREAKER','SPECIALIST','OWNER'} else 'MANUAL_AUTHORITY','commit_sha':sha,'review_subject_digest':digest,'created_at':'2026-09-02T00:00:00Z','verdict':verdict,'active':True}

    def test_same_tree_same_digest(self):
        self.assertEqual(self.digest(),self.digest())

    def test_material_changes_change_digest(self):
        base=self.digest()
        cases=['src/code.py','tests/verification/test_dummy.py','docs/requirement.md','.github/workflows/verification-gate.yml','.steward/verification-policy.yaml']
        for rel in cases:
            original=(self.root/rel).read_bytes(); (self.root/rel).write_bytes(original+b'\n# material-change\n')
            self.assertNotEqual(base,self.digest(),rel); (self.root/rel).write_bytes(original)
            self.assertEqual(base,self.digest(),rel)

    def test_state_and_reports_do_not_change_digest(self):
        base=self.digest()
        p=self.root/'verification/state.yaml'; p.write_text(p.read_text()+'\n',encoding='utf-8')
        (self.root/'verification/reports/PASS').write_text('PASS\n',encoding='utf-8'); git(self.root,'add','verification/reports/PASS')
        self.assertEqual(base,self.digest())

    def test_stale_breaker_digest_rejected(self):
        d=self.digest(); ev=self.builder_evidence(d,role='FRESH_BREAKER',producer='BREAKER',etype='BREAKER_VERDICT',verdict='PASS')
        self.write_state(status='READY_FOR_OWNER_ACCEPTANCE',previous_status='INDEPENDENT_REVIEW_PENDING',transition={'from':'INDEPENDENT_REVIEW_PENDING','to':'READY_FOR_OWNER_ACCEPTANCE','authority':'FRESH_BREAKER'},review_subject_digest=d,evidence=[ev])
        (self.root/'src/code.py').write_text('VALUE = 2\n',encoding='utf-8')
        with self.assertRaises(self.v.VerificationError): self.v.validate()

    def test_pass_file_alone_has_no_authority(self):
        (self.root/'verification/reports/PASS').write_text('PASS\n',encoding='utf-8'); git(self.root,'add','verification/reports/PASS')
        d=self.digest(); self.write_state(status='READY_FOR_OWNER_ACCEPTANCE',previous_status='INDEPENDENT_REVIEW_PENDING',transition={'from':'INDEPENDENT_REVIEW_PENDING','to':'READY_FOR_OWNER_ACCEPTANCE','authority':'FRESH_BREAKER'},review_subject_digest=d,evidence=[])
        with self.assertRaises(self.v.VerificationError): self.v.validate()

    def test_builder_cannot_claim_breaker_or_owner(self):
        d=self.digest()
        for role,etype in [('FRESH_BREAKER','BREAKER_VERDICT'),('OWNER','ACCEPTANCE')]:
            ev=self.builder_evidence(d,role=role,producer='BUILDER',etype=etype)
            self.write_state(status='READY_FOR_OWNER_ACCEPTANCE',previous_status='INDEPENDENT_REVIEW_PENDING',transition={'from':'INDEPENDENT_REVIEW_PENDING','to':'READY_FOR_OWNER_ACCEPTANCE','authority':'FRESH_BREAKER'},review_subject_digest=d,evidence=[ev])
            with self.assertRaises(self.v.VerificationError): self.v.validate()

    def test_invalid_transition_rejected(self):
        d=self.digest(); self.write_state(status='MERGED',previous_status='BUILDING',transition={'from':'BUILDING','to':'MERGED','authority':'BUILDER'},review_subject_digest=d,evidence=[])
        with self.assertRaises(self.v.VerificationError): self.v.validate()

    def test_conflict_markers_rejected(self):
        (self.root/'src/code.py').write_text('<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n',encoding='utf-8')
        with self.assertRaises(self.v.VerificationError): self.v.validate()

    def test_symlink_target_changes_digest_without_symlink_privilege(self):
        # Represent a tracked symlink directly in the Git index. This works on
        # Windows even when creating filesystem symlinks is not permitted.
        blob1=git(self.root,'hash-object','-w','--stdin',input=b'target-a').stdout.decode().strip()
        git(self.root,'update-index','--add','--cacheinfo',f'120000,{blob1},link')
        d1=self.digest()
        blob2=git(self.root,'hash-object','-w','--stdin',input=b'target-b').stdout.decode().strip()
        git(self.root,'update-index','--cacheinfo',f'120000,{blob2},link')
        d2=self.digest()
        self.assertNotEqual(d1,d2)

    def test_repeated_validation_idempotent(self):
        self.v.validate(); self.v.validate(); self.assertEqual(self.digest(),self.digest())

    def test_initial_baseline_self_token_is_root_builder_evidence_only(self):
        d=self.digest(); token='INITIAL_BASELINE_BOOTSTRAP_SELF'
        ev=self.builder_evidence(d)
        ev.update({'id':'BOOTSTRAP-BUILDER-REPORT','commit_sha':token,'verdict':'BUILDER_GREEN'})
        self.write_state(status='INDEPENDENT_REVIEW_PENDING',previous_status='BUILDING',transition={'from':'BUILDING','to':'INDEPENDENT_REVIEW_PENDING','authority':'BUILDER'},review_subject_digest=d,review_subject_commit=token,evidence=[ev])
        self.v.validate()
        ev['active']=False
        self.write_state(status='BUILDING',previous_status='INDEPENDENT_REVIEW_PENDING',transition={'from':'INDEPENDENT_REVIEW_PENDING','to':'BUILDING','authority':'BUILDER'},review_subject_digest='',review_subject_commit='',evidence=[ev])
        git(self.root,'add','verification/state.yaml')
        git(self.root,'commit','-qm','metadata commit makes HEAD non-root')
        self._load()
        self.v.validate()
        ev['active']=True
        self.write_state(status='INDEPENDENT_REVIEW_PENDING',previous_status='BUILDING',transition={'from':'BUILDING','to':'INDEPENDENT_REVIEW_PENDING','authority':'BUILDER'},review_subject_digest=d,review_subject_commit=token,evidence=[ev])
        with self.assertRaises(self.v.VerificationError): self.v.validate()

    def test_workflows_fetch_full_history_for_evidence_commits(self):
        workflow_dir=REPO/'.github'/'workflows'
        workflows=[workflow_dir/'verification-gate.yml']
        workflows.extend(p for p in (workflow_dir/'bootstrap-integrity.yml',workflow_dir/'steward-baseline.yml') if p.exists())
        for workflow in workflows:
            self.assertIn('fetch-depth: 0',workflow.read_text(encoding='utf-8'),workflow)

if __name__=='__main__': unittest.main(verbosity=2)
