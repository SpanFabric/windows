# Fresh BREAKER handoff — missing-parent fixture

Repository: SpanFabric/windows
Base: f260f7e7a950d46f6efbc08a576e2383e94cfb24
Material: 38b9695b82570b4ba50b529492baf50cac755390
Digest: 99e8b432958a0d930fd5394f6052f19410da6f84ce412df1161fed9b88688cb2
Changed material: tests/verification/test_lifecycle_history.py
Builder ceiling: INDEPENDENT_REVIEW_PENDING

Reconstruct independently; Builder claims and CI are untrusted until checked.
Attack loose/GC/repack storage, GC commit-graph exit0 with incomplete traversal, paired valid/corrupt tips, exact absent 40-hex nonzero grandparent, successful base guard, strict direct+immutable runner history rejection, and positive restoration with malformed objects still present but unreachable.
Reproduce historical setup failure after GC without rerunning/deleting historical Actions run39800181504. Confirm validator/policy/runner/workflows unchanged, full authority/shallow/root/history regressions, old Owner/Breaker evidence inactive, material digest exact, and exact new PR-head verification-gate/bootstrap-integrity/windows prerequisite success.
Do not remediate, merge, accept as Owner, touch UP-003 or persistence PRs, or start MIG-001.
