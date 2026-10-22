"""Durable Git-history regressions for SHARED-FB-001; no network or mocks."""
import json
import pathlib
import unittest
import test_verify_review_state as bridge


class LifecycleHistoryTests(unittest.TestCase):
    def setUp(self):
        self.b=bridge.BridgeTests(); self.b.setUp()
        self.root=self.b.root; self.v=self.b.v; self.serial=0
        self.base=self.b.head()

    def tearDown(self):
        self.b.tearDown()

    def git(self,*args,**kw):
        return bridge.git(self.root,*args,**kw)

    def read(self,path):
        return json.loads((self.root/path).read_text(encoding='utf-8'))

    def write(self,path,value):
        (self.root/path).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')

    def commit(self,label='state'):
        self.serial+=1; self.git('add','-A')
        self.git('commit','--allow-empty','-qm',f'{self.serial}: {label}')
        return self.b.head()

    def record(self,status,previous,authority,commit=True,material=False):
        if material:
            (self.root/'src/code.py').write_text(f'VALUE = {self.serial+10}\n',encoding='utf-8')
            self.git('add','src/code.py')
        evidence=[]; digest=self.b.digest(); sha=self.b.head()
        role={'INDEPENDENT_REVIEW_PENDING':'BUILDER','READY_FOR_OWNER_ACCEPTANCE':'FRESH_BREAKER',
              'ACCEPTED':'OWNER','BREAKER_FAILED':'FRESH_BREAKER'}.get(status)
        if role:
            kind={'BUILDER':'BUILDER_REPORT','FRESH_BREAKER':'BREAKER_VERDICT','OWNER':'ACCEPTANCE'}[role]
            evidence=[self.b.evidence(digest,role,role,kind,'BUILDER_GREEN' if role=='BUILDER' else 'PASS')]
        if status in {'MERGED','POST_MERGE_VERIFIED','MERGE_VERIFICATION_FAILED'}:
            owner,gate,sha=self.b.owner_and_merge_evidence(digest)
            evidence=[owner,gate]
            if status!='MERGED':
                evidence.append(self.b.evidence(digest,'POST_MERGE_WORKFLOW','POST_MERGE_WORKFLOW',
                    'POST_MERGE_VERIFICATION','PASS' if status=='POST_MERGE_VERIFIED' else 'FAIL',sha,sha))
        self.b.write_state(status=status,previous_status=previous,
            transition={'from':previous,'to':status,'authority':authority},
            review_subject_digest=digest,review_subject_commit=sha,evidence=evidence,
            resulting_merge_commit_sha=sha)
        return self.commit() if commit else None

    def accepted(self):
        for to,frm,role in [('BUILDING','PLANNED','BUILDER'),
                ('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER'),
                ('READY_FOR_OWNER_ACCEPTANCE','INDEPENDENT_REVIEW_PENDING','FRESH_BREAKER'),
                ('ACCEPTED','READY_FOR_OWNER_ACCEPTANCE','OWNER')]:
            self.record(to,frm,role)
        self.assertEqual(0,self.b.cli('--check','--base',self.base).returncode)

    def check(self,valid,trusted=False,base=None):
        args=('--check','--base',base or self.base)
        result=self.b.trusted_cli('--base',base or self.base) if trusted else self.b.cli(*args)
        self.assertEqual(0 if valid else 2,result.returncode,result.stdout+result.stderr)
        if not valid:self.assertIn('Lifecycle history:',result.stderr)

    def test_hidden_wrong_role_reopens_fail_direct_and_trusted(self):
        for role in ('OWNER','CI','FRESH_BREAKER'):
            for material in (False,True):
                with self.subTest(role=role,material=material):
                    # Each subcase owns a distinct durable graph, not a reset of a failed chain.
                    case=LifecycleHistoryTests(); case.setUp()
                    try:
                        case.accepted(); case.record('BUILDING','ACCEPTED',role,material=material)
                        rejected=case.b.cli('--check','--base',case.base)
                        self.assertEqual(2,rejected.returncode,rejected.stderr)
                        case.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER')
                        case.check(False); case.check(False,trusted=True)
                        case.check(False,base=case.b.head())
                    finally:case.tearDown()

    def test_normal_accepted_reopen_and_irp(self):
        self.accepted();self.record('BUILDING','ACCEPTED','BUILDER',material=True)
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER')
        self.check(True);self.check(True,trusted=True)

    def test_skipped_commits_cannot_hide_invalid_transition(self):
        self.accepted();self.record('BUILDING','ACCEPTED','CI')
        for n in range(3):
            (self.root/f'docs/unrelated-{n}.md').write_text('unrelated\n',encoding='utf-8')
            self.commit('unrelated retained state')
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER')
        self.check(False);self.check(False,trusted=True)

    def test_later_policy_grant_cannot_retroactively_legalize(self):
        self.accepted();policy=self.read('.steward/verification-policy.yaml')
        rule=next(x for x in policy['transitions'] if x['from']=='ACCEPTED' and x['to']=='BUILDING')
        policy['transitions'].remove(rule);self.write('.steward/verification-policy.yaml',policy);self.commit('revoke prospectively')
        self.record('BUILDING','ACCEPTED','BUILDER')
        policy['transitions'].append(rule);self.write('.steward/verification-policy.yaml',policy);self.commit('later grant')
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER')
        self.check(False);self.check(False,trusted=True)

    def test_policy_grant_is_atomic_and_revocation_not_retroactive(self):
        self.accepted();policy=self.read('.steward/verification-policy.yaml')
        rule=next(x for x in policy['transitions'] if x['from']=='ACCEPTED' and x['to']=='BUILDING')
        policy['transitions'].remove(rule);self.write('.steward/verification-policy.yaml',policy);self.commit('remove unused edge')
        policy['transitions'].append(rule);self.write('.steward/verification-policy.yaml',policy)
        self.record('BUILDING','ACCEPTED','BUILDER')
        policy['transitions'].remove(rule);self.write('.steward/verification-policy.yaml',policy);self.commit('revoke consumed edge')
        self.check(True)
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER')
        self.check(True);self.check(True,trusted=True)

    def test_policy_cannot_widen_role_graph_or_change_history_version(self):
        self.accepted();p=self.read('.steward/verification-policy.yaml')
        next(x for x in p['transitions'] if x['from']=='ACCEPTED' and x['to']=='BUILDING')['authorities']=['OWNER']
        self.write('.steward/verification-policy.yaml',p);self.record('BUILDING','ACCEPTED','OWNER')
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER');self.check(False)

    def test_unknown_historical_version_cannot_be_hidden(self):
        self.accepted();p=self.read('.steward/verification-policy.yaml');p['schema_version']='99'
        self.write('.steward/verification-policy.yaml',p);self.commit('unknown historical policy')
        p['schema_version']='1.0';self.write('.steward/verification-policy.yaml',p)
        self.record('BUILDING','ACCEPTED','BUILDER');self.check(False)

    def test_unknown_history_contract_cannot_be_hidden(self):
        self.accepted();p=self.read('.steward/verification-policy.yaml')
        p['history_validation']['model']='unknown-future-model'
        self.write('.steward/verification-policy.yaml',p);self.commit('unknown historical contract')
        p['history_validation']['model']='durable-git-dag-v1'
        self.write('.steward/verification-policy.yaml',p)
        self.record('BUILDING','ACCEPTED','BUILDER');self.check(False)

    def merge_object(self,parents):
        tree=self.git('write-tree').stdout.decode().strip()
        args=['commit-tree',tree,'-m','explicit merge fixture']
        for parent in parents:args+=['-p',parent]
        sha=self.git(*args).stdout.decode().strip();self.git('update-ref','HEAD',sha);return sha

    def test_ancestor_parent_adoption_preserves_validated_chain(self):
        self.accepted();selected=self.b.head();self.merge_object([self.base,selected])
        self.check(True);self.check(True,trusted=True)

    def test_merge_cannot_erase_invalid_parent_history(self):
        self.accepted();self.record('BUILDING','ACCEPTED','OWNER')
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER')
        self.merge_object([self.base,self.b.head()]);self.check(False);self.check(False,trusted=True)

    def test_divergent_merge_fails_closed_even_with_same_state(self):
        self.accepted();fork=self.b.head();self.commit('left');left=self.b.head()
        tree=self.git('write-tree').stdout.decode().strip()
        right=self.git('commit-tree',tree,'-p',fork,'-m','right divergent parent').stdout.decode().strip()
        self.merge_object([left,right]);self.check(False);self.check(False,trusted=True)

    def test_merge_cannot_introduce_an_unattributed_state(self):
        self.accepted();parent=self.b.head()
        self.record('BUILDING','ACCEPTED','BUILDER',commit=False);self.git('add','verification/state.yaml')
        self.merge_object([self.base,parent]);self.check(False)

    def test_shallow_history_fails_even_without_base(self):
        self.accepted();(self.root/'.git/shallow').write_text(self.b.head()+'\n',encoding='ascii')
        with self.assertRaisesRegex(self.v.VerificationError,'shallow'):
            self.v.validate_lifecycle_history(self.read('.steward/verification-policy.yaml'),self.read('verification/state.yaml'))
        self.assertEqual(2,self.b.cli('--check','--base',self.b.head()).returncode)

    def test_missing_historical_state_is_not_an_empty_history(self):
        self.accepted();saved=(self.root/'verification/state.yaml').read_bytes()
        self.git('rm','verification/state.yaml');self.commit('missing state')
        (self.root/'verification/state.yaml').write_bytes(saved);self.record('BUILDING','ACCEPTED','BUILDER')
        self.check(False);self.check(False,trusted=True)

    def test_unreachable_live_state_is_rejected(self):
        self.accepted();self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER',commit=False)
        self.check(False);self.check(False,trusted=True)

    def test_merged_post_merge_and_failure_recovery_histories(self):
        self.accepted();self.record('MERGED','ACCEPTED','MERGE_HOSTING_GATE');self.check(True)
        merged=self.b.head();self.record('POST_MERGE_VERIFIED','MERGED','POST_MERGE_WORKFLOW')
        self.check(True);self.check(True,trusted=True)
        # Separate branch of durable history for failed post-merge recovery.
        self.git('checkout','-qb','failed-post',merged)
        self.record('MERGE_VERIFICATION_FAILED','MERGED','POST_MERGE_WORKFLOW');self.check(True)
        self.record('BUILDING','MERGE_VERIFICATION_FAILED','BUILDER')
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER');self.check(True)

    def test_root_planned_bootstrap_and_nonroot_null_base(self):
        self.check(True)
        digest=self.b.digest();token='INITIAL_BASELINE_BOOTSTRAP_SELF';ev=self.b.builder_evidence(digest);ev['commit_sha']=token
        self.b.write_state(status='INDEPENDENT_REVIEW_PENDING',previous_status='BUILDING',
            transition={'from':'BUILDING','to':'INDEPENDENT_REVIEW_PENDING','authority':'BUILDER'},
            review_subject_digest=digest,review_subject_commit=token,evidence=[ev])
        self.git('add','verification/state.yaml');tree=self.git('write-tree').stdout.decode().strip()
        root=self.git('commit-tree',tree,'-m','explicit bootstrap root fixture').stdout.decode().strip()
        self.git('update-ref','HEAD',root)
        self.base=root
        self.check(True,trusted=True,base='0'*40)
        self.record('BUILDING','INDEPENDENT_REVIEW_PENDING','BUILDER')
        self.check(True);self.assertEqual(2,self.b.trusted_cli('--base','0'*40).returncode)

    def test_replace_refs_do_not_hide_original_invalid_commit(self):
        self.accepted();accepted=self.b.head();self.record('BUILDING','ACCEPTED','OWNER');invalid=self.b.head()
        self.record('INDEPENDENT_REVIEW_PENDING','BUILDING','BUILDER')
        self.git('replace',invalid,accepted)
        self.check(False);self.check(False,trusted=True)

    def test_grafted_history_is_rejected(self):
        self.accepted();(self.root/'.git/info/grafts').write_text(self.b.head()+'\n',encoding='ascii')
        self.check(False,base=self.b.head())

    def test_octopus_adoption_fails_closed(self):
        self.accepted();tip=self.b.head()
        middle=self.git('rev-parse','HEAD^').stdout.decode().strip()
        self.merge_object([self.base,middle,tip]);self.check(False)

    def test_duplicate_historical_json_is_rejected(self):
        self.accepted();p=self.root/'verification/state.yaml'
        text=p.read_text(encoding='utf-8');p.write_text(text.replace('{','{"status":"ACCEPTED",',1),encoding='utf-8')
        self.commit('ambiguous JSON snapshot')
        self.record('BUILDING','ACCEPTED','BUILDER');self.check(False)

    def test_nonregular_historical_state_is_rejected(self):
        self.accepted();oid=self.git('rev-parse','HEAD:verification/state.yaml').stdout.decode().strip()
        self.git('update-index','--cacheinfo',f'120000,{oid},verification/state.yaml')
        self.git('commit','-qm','nonregular state snapshot')
        self.record('BUILDING','ACCEPTED','BUILDER');self.check(False)

    def test_missing_required_parent_object_fails_closed(self):
        self.accepted();object_path=self.root/'.git/objects'/self.base[:2]/self.base[2:]
        held=object_path.with_suffix('.held');object_path.rename(held)
        try:
            p=self.b.cli('--check','--base',self.b.head());self.assertEqual(2,p.returncode,p.stderr)
        finally:held.rename(object_path)

    def test_accepted_root_cannot_replace_real_bootstrap(self):
        self.accepted();tree=self.git('write-tree').stdout.decode().strip()
        root=self.git('commit-tree',tree,'-m','forged accepted root').stdout.decode().strip()
        self.git('update-ref','HEAD',root);self.check(False,base=root)


if __name__=='__main__':
    unittest.main()
