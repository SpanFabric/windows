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

    def cli(self,*args):
        return subprocess.run([sys.executable,str(self.root/'scripts/verify_review_state.py'),*args],cwd=self.root,capture_output=True,text=True)

    def evidence(self,digest,producer='BUILDER',role='BUILDER',etype='BUILDER_REPORT',verdict='BUILDER_GREEN',commit_sha=None,merge_commit_sha=None):
        sha=commit_sha or git(self.root,'rev-parse','HEAD').stdout.decode().strip()
        evidence={'id':f'EV-{producer}-{etype}','type':etype,'producer':producer,'role':role,'authority':'MANUAL_AUTHORITY','commit_sha':sha,'review_subject_digest':digest,'created_at':'2026-09-02T00:00:00Z','verdict':verdict,'active':True}
        if merge_commit_sha is not None: evidence['merge_commit_sha']=merge_commit_sha
        return evidence

    def builder_evidence(self,digest,role='BUILDER',producer='BUILDER',etype='BUILDER_REPORT',verdict='BUILDER_GREEN'):
        return self.evidence(digest,producer,role,etype,verdict)

    def owner_and_merge_evidence(self,digest,merge_sha=None):
        sha=merge_sha or git(self.root,'rev-parse','HEAD').stdout.decode().strip()
        owner=self.evidence(digest,'OWNER','OWNER','ACCEPTANCE','PASS',sha)
        gate=self.evidence(digest,'MERGE_HOSTING_GATE','MERGE_HOSTING_GATE','MERGE_STATE','MERGED',sha,sha)
        return owner,gate,sha

    def test_same_tree_same_digest(self):
        self.assertEqual(self.digest(),self.digest())

    def test_material_changes_change_digest(self):
        base=self.digest()
        cases=['src/code.py','tests/verification/test_dummy.py','docs/requirement.md','.github/workflows/verification-gate.yml','.steward/verification-policy.yaml']
        for rel in cases:
            original=(self.root/rel).read_bytes(); (self.root/rel).write_bytes(original+b'\n# material-change\n')
            with self.assertRaisesRegex(self.v.VerificationError,'Unstaged material changes',msg=rel): self.v.validate()
            git(self.root,'add',rel)
            self.assertNotEqual(base,self.digest(),rel); (self.root/rel).write_bytes(original); git(self.root,'add',rel)
            self.assertEqual(base,self.digest(),rel)

    def test_state_and_reports_do_not_change_digest(self):
        base=self.digest()
        p=self.root/'verification/state.yaml'; p.write_text(p.read_text()+'\n',encoding='utf-8')
        (self.root/'verification/reports/PASS').write_text('PASS\n',encoding='utf-8'); git(self.root,'add','verification/reports/PASS')
        self.assertEqual(base,self.digest())

    def test_stale_breaker_digest_rejected(self):
        d=self.digest(); ev=self.builder_evidence(d,role='FRESH_BREAKER',producer='FRESH_BREAKER',etype='BREAKER_VERDICT',verdict='PASS')
        self.write_state(status='READY_FOR_OWNER_ACCEPTANCE',previous_status='INDEPENDENT_REVIEW_PENDING',transition={'from':'INDEPENDENT_REVIEW_PENDING','to':'READY_FOR_OWNER_ACCEPTANCE','authority':'FRESH_BREAKER'},review_subject_digest=d,review_subject_commit=ev['commit_sha'],evidence=[ev])
        (self.root/'src/code.py').write_text('VALUE = 2\n',encoding='utf-8')
        with self.assertRaisesRegex(self.v.VerificationError,'Unstaged material changes'): self.v.validate()

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
        git(self.root,'add','src/code.py')
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

    def test_literal_backslash_metadata_lookalikes_fail_closed(self):
        for rel in [r'verification\reports\payload.py',r'verification\state.yaml']:
            blob=git(self.root,'hash-object','-w','--stdin',input=b'lookalike').stdout.decode().strip()
            added=git(self.root,'update-index','--add','--cacheinfo',f'100644,{blob},{rel}',check=False)
            if added.returncode:
                # Windows Git rejects the path before it reaches the index;
                # Ubuntu CI exercises the complete index-to-digest route.
                with self.assertRaises(self.v.VerificationError): self.v.canonical_git_path(rel.encode('utf-8'))
                continue
            with self.assertRaises(self.v.VerificationError): self.v.compute_review_subject_digest()
            git(self.root,'update-index','--force-remove','--',rel)

    def test_exclusion_lookalikes_are_material(self):
        base=self.digest()
        lookalikes=['verification/reports-evil/file.py','verification/state.yaml.bak']
        for rel in lookalikes:
            path=self.root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('material\n',encoding='utf-8'); git(self.root,'add',rel)
        digest,material=self.v.compute_review_subject_digest()
        self.assertNotEqual(base,digest)
        for rel in lookalikes: self.assertIn(rel,material)

    def test_unknown_base_fails_closed_via_cli(self):
        p=self.cli('--check','--base','f'*40)
        self.assertEqual(2,p.returncode,p.stderr)
        self.assertIn('does not resolve',p.stderr)

    def test_unrelated_base_fails_closed_via_cli(self):
        tree=git(self.root,'rev-parse','HEAD^{tree}').stdout.decode().strip()
        unrelated=git(self.root,'commit-tree',tree,'-m','unrelated').stdout.decode().strip()
        p=self.cli('--check','--base',unrelated)
        self.assertEqual(2,p.returncode,p.stderr)
        self.assertIn('not an available ancestor',p.stderr)

    def test_shallow_history_fails_closed_via_cli(self):
        head=git(self.root,'rev-parse','HEAD').stdout.decode().strip()
        (self.root/'.git/shallow').write_text(head+'\n',encoding='ascii')
        p=self.cli('--check','--base',head)
        self.assertEqual(2,p.returncode,p.stderr)
        self.assertIn('shallow',p.stderr)

    def test_valid_base_and_tightly_scoped_null_bootstrap_via_cli(self):
        head=git(self.root,'rev-parse','HEAD').stdout.decode().strip()
        self.assertEqual(0,self.cli('--check','--base',head).returncode)
        d=self.digest(); token='INITIAL_BASELINE_BOOTSTRAP_SELF'; ev=self.builder_evidence(d)
        ev.update({'id':'ROOT-BOOTSTRAP','commit_sha':token})
        self.write_state(status='INDEPENDENT_REVIEW_PENDING',previous_status='BUILDING',transition={'from':'BUILDING','to':'INDEPENDENT_REVIEW_PENDING','authority':'BUILDER'},review_subject_digest=d,review_subject_commit=token,evidence=[ev])
        p=self.cli('--check','--base','0'*40)
        self.assertEqual(0,p.returncode,p.stderr)

    def test_closed_producer_role_type_matrix_rejects_impersonation(self):
        d=self.digest(); good=self.builder_evidence(d)
        cases=[
            ('CI','FRESH_BREAKER','BREAKER_VERDICT'),
            ('BUILDER','FRESH_BREAKER','BREAKER_VERDICT'),
            ('BUILDER','OWNER','ACCEPTANCE'),
            ('OWNER','MERGE_HOSTING_GATE','MERGE_STATE'),
            ('CI','OWNER','ACCEPTANCE'),
            ('MERGE_HOSTING_GATE','POST_MERGE_WORKFLOW','POST_MERGE_VERIFICATION'),
        ]
        for producer,role,etype in cases:
            bad=self.evidence(d,producer,role,etype,'PASS')
            self.write_state(status='INDEPENDENT_REVIEW_PENDING',previous_status='BUILDING',transition={'from':'BUILDING','to':'INDEPENDENT_REVIEW_PENDING','authority':'BUILDER'},review_subject_digest=d,review_subject_commit=good['commit_sha'],evidence=[good,bad])
            with self.assertRaises(self.v.VerificationError,msg=f'{producer}->{role}/{etype}'): self.v.validate()

    def test_valid_builder_and_ci_evidence_remain_nonindependent_technical_evidence(self):
        d=self.digest(); builder=self.builder_evidence(d); ci=self.evidence(d,'CI','CI','CI_RESULT','PASS')
        self.write_state(status='INDEPENDENT_REVIEW_PENDING',previous_status='BUILDING',transition={'from':'BUILDING','to':'INDEPENDENT_REVIEW_PENDING','authority':'BUILDER'},review_subject_digest=d,review_subject_commit=builder['commit_sha'],evidence=[builder,ci])
        self.v.validate()

    def test_merge_and_post_merge_states_require_sha_bound_evidence(self):
        d=self.digest(); owner,gate,sha=self.owner_and_merge_evidence(d)
        merged={'status':'MERGED','previous_status':'ACCEPTED','transition':{'from':'ACCEPTED','to':'MERGED','authority':'MERGE_HOSTING_GATE'},'review_subject_digest':d,'review_subject_commit':sha,'resulting_merge_commit_sha':sha}
        self.write_state(**merged,evidence=[owner])
        with self.assertRaises(self.v.VerificationError): self.v.validate()
        missing_sha=dict(merged,resulting_merge_commit_sha='')
        self.write_state(**missing_sha,evidence=[owner,gate])
        with self.assertRaises(self.v.VerificationError): self.v.validate()
        tree=git(self.root,'rev-parse','HEAD^{tree}').stdout.decode().strip()
        wrong=git(self.root,'commit-tree',tree,'-m','wrong merge').stdout.decode().strip()
        wrong_gate=self.evidence(d,'MERGE_HOSTING_GATE','MERGE_HOSTING_GATE','MERGE_STATE','MERGED',wrong,wrong)
        self.write_state(**merged,evidence=[owner,wrong_gate])
        with self.assertRaises(self.v.VerificationError): self.v.validate()
        self.write_state(**merged,evidence=[owner,gate])
        self.v.validate()
        post={'status':'POST_MERGE_VERIFIED','previous_status':'MERGED','transition':{'from':'MERGED','to':'POST_MERGE_VERIFIED','authority':'POST_MERGE_WORKFLOW'},'review_subject_digest':d,'review_subject_commit':sha,'resulting_merge_commit_sha':sha}
        self.write_state(**post,evidence=[owner])
        with self.assertRaises(self.v.VerificationError): self.v.validate()
        post_pass=self.evidence(d,'POST_MERGE_WORKFLOW','POST_MERGE_WORKFLOW','POST_MERGE_VERIFICATION','PASS',sha,sha)
        self.write_state(**post,evidence=[owner,gate,post_pass])
        self.v.validate()
        failed=dict(post,status='MERGE_VERIFICATION_FAILED',transition={'from':'MERGED','to':'MERGE_VERIFICATION_FAILED','authority':'POST_MERGE_WORKFLOW'})
        post_fail=self.evidence(d,'POST_MERGE_WORKFLOW','POST_MERGE_WORKFLOW','POST_MERGE_VERIFICATION','FAIL',sha,sha)
        self.write_state(**failed,evidence=[owner,gate,post_fail])
        self.v.validate()
        self.write_state(**post,evidence=[owner,gate,post_fail])
        with self.assertRaises(self.v.VerificationError): self.v.validate()

    def test_workflows_fetch_full_history_for_evidence_commits(self):
        workflow_dir=REPO/'.github'/'workflows'
        workflows=[workflow_dir/'verification-gate.yml']
        workflows.extend(p for p in (workflow_dir/'bootstrap-integrity.yml',workflow_dir/'steward-baseline.yml') if p.exists())
        for workflow in workflows:
            self.assertIn('fetch-depth: 0',workflow.read_text(encoding='utf-8'),workflow)

if __name__=='__main__': unittest.main(verbosity=2)
