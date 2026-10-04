# Fresh BREAKER handoff — Manual Verification Bridge bootstrap

## Role and independence

Act as a fresh independent BREAKER. Do not rely on Builder reasoning or create
an acceptance verdict by inference. Inspect the exact repository state and
return a durable report with the checked SHA, scope, commands, attacks,
findings, confirmed constructions, and a verdict.

## Review subject

- Repository: `SpanFabric/windows`
- Accepted bootstrap `main` root: `e3fd92a90238da183fc1ff8ff568301fcb18dd1f`
- Open PR: `#1`, branch `codex/bootstrap-verification-bridge`
- Material review-subject commit: `a33bf8a3a909ac9e08025836135741753ddb07da`
- Review-subject SHA-256: `c95aed115b94c33acf66dc340accd99059c9122f98d2cc894c79d84462d43219`
- Declared risk: `R2`

The PR head contains excluded report/state metadata. The material commit above
and the PR head must compute the same review-subject digest.

## Scope and invariants to verify

1. The initial parentless `main` baseline contains the repository-local Manual
   Verification Bridge and was the one-time `INITIAL_BASELINE_BOOTSTRAP`.
2. The bridge hashes all material tracked files deterministically, excluding
   only `verification/state.yaml` and `verification/reports/**`; policy,
   validator, tests, workflows, requirements, and configuration remain material.
3. Builder evidence cannot assert Breaker, Specialist, Owner, merge, or
   post-merge authority. A PASS-named file has no authority by itself.
4. The bootstrap self-commit token is active only on the parentless first
   commit; later inactive historical records cannot satisfy state requirements.
5. A material change returns `INDEPENDENT_REVIEW_PENDING` to `BUILDING`,
   invalidating active evidence before a new Builder report is recorded.
6. CI fetches full Git history before validating commit-bound evidence; shallow
   checkout must fail closed rather than silently accepting unknown commits.
7. No materializer Apply path was executed; F-000-04 remains quarantined in
   `SpanFabric/docs`.

## Reproduction commands

```text
python -m unittest discover -s tests/verification -p 'test_*.py' -v
python scripts/verify_review_state.py --check
git diff --check main...HEAD
git status --short
```

Also inspect current PR CI, all evidence commit references, review-subject
digest stability under report/state-only edits, merge-conflict marker rejection,
and the regression proving `fetch-depth: 0` in all validation workflows.

## Required outcome

Do not merge. A passing BREAKER may move only to `READY_FOR_OWNER_ACCEPTANCE`
under the policy and must explicitly preserve `MANUAL_AUTHORITY` limitations.
If a finding is confirmed, document its failure sequence, violated invariant,
impact, severity, remediation, and fresh regression evidence.
