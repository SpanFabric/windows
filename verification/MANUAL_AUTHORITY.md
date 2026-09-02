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
