# AGENTS — windows
## Mission
Windows WDDM KMD/UMD/ICD integration, local client service integration, adapter/resource/presentation/recovery behavior.

## Owned scope
WDDM adapter, KMD, Windows UMD/ICD adaptations, RGPU_DRIVER_ABI, Windows presentation integration

## Forbidden scope / special rules
Own WDDM/client integration. No injection/launcher shortcuts. Kernel logic is minimized; network/TLS/cache belongs outside KMD absent new TD. Hardware proof uses sacrificial runner.

## Required reading
Root AGENTS, active phase, linked TDs/gates, architecture invariants, repository contract, relevant interfaces/boundary audits.

## Test obligations
Run all phase-required tests affecting this repo and any contract/version-skew/negative tests implied by changed boundaries. Hardware-dependent claims require hardware evidence.

## Cross-repo rules
Change another repo only when the phase permits it and use the cross-repo change template. Update canonical contracts first or atomically with compatible implementations.

## Proof/failure rules
Builder green => `INDEPENDENT_REVIEW_PENDING`. Fresh BREAKER required. Blocked tests remain blocked; no inferred success.
## Manual Verification Bridge v1
- `.steward/verification-policy.yaml` is the repository-local machine-readable verification policy until the central Project Steward assumes enforcement.
- Critical changes require a non-default branch and PR. The accepted default branch is the baseline unless canonical governance explicitly says otherwise.
- Builder, CI, fresh BREAKER, specialist, owner, merge gate and post-merge workflow authorities are distinct. Builders MUST NOT self-attest independent evidence or acceptance.
- Any material change to code, tests, requirements/invariants, schemas/migrations, workflows, executable scripts or relevant configuration invalidates prior review evidence by changing the Review-Subject-Digest.
- State model: `PLANNED → BUILDING → INDEPENDENT_REVIEW_PENDING → (BREAKER_FAILED | READY_FOR_OWNER_ACCEPTANCE) → ACCEPTED → MERGED → POST_MERGE_VERIFIED`, with `MERGE_VERIFICATION_FAILED` for failed merged-commit verification.
- Merge, rebase and conflict resolution are verification boundaries. Post-merge verification MUST target the actual merged commit.
- During this temporary bridge, manually checked independent/owner authority MUST be marked `MANUAL_AUTHORITY`; a `PASS` file or Builder-authored verdict is never independent evidence.
- Before push/merge, run the canonical trusted pre-execution command in `verification/MANUAL_AUTHORITY.md` with the actual canonical base SHA, then the repository verification tests. Direct `python scripts/verify_review_state.py` execution is non-authoritative defense in depth only. Green Builder/CI results mean at most `INDEPENDENT_REVIEW_PENDING`.
