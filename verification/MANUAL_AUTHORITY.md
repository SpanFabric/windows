# Manual Authority — Verification Bridge v1

This repository uses a temporary repository-local verification bridge until the central SpanFabric Project Steward takes over the same state/evidence formats.

`MANUAL_AUTHORITY` is explicit: identity/authority of fresh BREAKER, specialist and owner acceptance is still checked by humans/hosting configuration, not cryptographically proven by this repository.

Required external settings that committed files cannot prove:

- Protect the default branch and require pull requests for critical changes.
- Require the `verification-gate` status check before merge once the repository exists.
- Restrict force-push/deletion of the default branch.
- Configure required independent reviewers/owners according to SpanFabric governance.
- Treat merge/rebase/conflict resolution as a new verification boundary and rerun required checks on the resulting commit.
- Run post-merge verification on the actual merged commit before marking `POST_MERGE_VERIFIED`.

Do not claim these settings are active merely because this file lists them.

## Initial baseline bootstrap exception

For exactly the first, parentless `main` commit, the policy permits the Builder
report's `commit_sha` to be `INITIAL_BASELINE_BOOTSTRAP_SELF`. The validator
accepts that token only for a `BUILDER_REPORT` which transitions `BUILDING` to
`INDEPENDENT_REVIEW_PENDING` while `HEAD` is that root commit. This solves the
first-commit self-reference without granting an independent or acceptance
verdict. Once a material change returns the state to `BUILDING`, the inactive
historical Builder record may retain the token but has no authority; every
later active report must name an existing immutable commit SHA.

## CI history boundary

CI validates evidence which may name a parent or earlier material commit while
the PR head contains excluded report metadata. Workflows therefore must fetch
full Git history (`fetch-depth: 0`); a shallow checkout cannot establish the
evidence commit's identity and must fail closed.

Every supplied non-null base SHA must resolve to a commit and be an available
ancestor of `HEAD`. An unknown base, unrelated history, or a shallow checkout
is a validation error, never an empty change set. The all-null base is allowed
only for the active parentless initial-baseline contract described above.

## Git path identity and review-subject exclusions

The review subject is derived from Git index/tree paths, not by normalizing
host filesystem paths. The only exclusions are the exact canonical Git path
`verification/state.yaml` and canonical slash-separated paths under
`verification/reports/`. A literal Git path containing a backslash is not the
same path; this bridge rejects it fail-closed rather than allowing it to
masquerade as excluded metadata. Index object identity, file mode, path and
blob content are all digest inputs. A material difference between the working
tree and that index fails closed; it must be staged before verification so
stale evidence cannot survive an unstaged local edit.

## Closed evidence authority matrix

`MANUAL_AUTHORITY` documents that the hosting service and humans still verify
identity. It is not a wildcard. The committed policy and validator enforce the
following fixed mapping: BUILDER/BUILDER/BUILDER_REPORT or REMEDIATION;
CI/CI/CI_RESULT; FRESH_BREAKER/FRESH_BREAKER/BREAKER_REPORT or
BREAKER_VERDICT; SPECIALIST/SPECIALIST/SPECIALIST_VERDICT;
OWNER/OWNER/ACCEPTANCE; MERGE_HOSTING_GATE/MERGE_HOSTING_GATE/MERGE_STATE;
and POST_MERGE_WORKFLOW/POST_MERGE_WORKFLOW/POST_MERGE_VERIFICATION. Unknown
or mismatched producer, role, type or authority fails closed.

## Merge and post-merge boundary

`resulting_merge_commit_sha` is the exact canonical commit SHA created by the
hosting merge operation. `MERGED` requires active
MERGE_HOSTING_GATE/MERGE_STATE evidence whose `commit_sha` and
`merge_commit_sha` both equal that state field. `POST_MERGE_VERIFIED` requires
that retained merge evidence plus successful POST_MERGE_WORKFLOW evidence
bound to the same SHA. Failed post-merge evidence may lead only to
`MERGE_VERIFICATION_FAILED`. Owner acceptance remains a prerequisite to a
normal merge transition but never substitutes for either boundary.

The future central Project Steward must preserve these field names and binding
rules when it imports repository-local state. It may add cryptographic identity
or hosting attestations, but cannot weaken the closed mapping or carry merge
evidence across a different resulting commit.
