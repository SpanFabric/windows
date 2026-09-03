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

An omitted base is represented as `None`; it is not an empty string and cannot
be used for an authoritative `--check`. Authoritative checks require an exact,
lowercase, 40-hex base SHA. Explicit empty, whitespace-only, surrounding-
whitespace, abbreviated, symbolic, uppercase, unknown, non-ancestor, and
ordinary-child all-zero bases fail closed. `--digest` without a base is only a
non-authoritative diagnostic and does not assert a history-derived risk floor.

## Trusted pre-execution runner

The authoritative local and CI command is the trusted pre-execution runner,
not `python scripts/verify_review_state.py`. A program running from the mutable
working tree cannot authenticate a complete replacement of itself after Python
has started.

The runner has this fixed sequence:

1. A host/CI shell materializes `scripts/trusted_verify_review_state.sh` from
   the immutable `HEAD` Git commit object into a private temporary file.
2. That temporary runner performs NUL-safe Git plumbing checks for unstaged
   tracked and untracked material paths before any repository Python executes.
   Only exact `verification/state.yaml` and canonical
   `verification/reports/**` paths remain non-material.
3. It verifies its own temporary-file Git object identity, materializes the
   validator from `HEAD:scripts/verify_review_state.py`, verifies that blob's
   object identity, and executes it from the private temporary directory with
   `python -I --repo-root <repository>`.
4. The trusted copy validates policy, state, evidence, digest and base-history
   contracts against the explicit repository root.

Use Git Bash (or another Bash host with Git and Python) locally. The following
is the canonical command; it never executes working-tree Python before the
preflight:

```bash
repo_root="$(git rev-parse --show-toplevel)"
base="$(git -C "$repo_root" rev-parse origin/main)"
trusted_dir="$(mktemp -d "${TMPDIR:-/tmp}/spangpu-trusted-runner.XXXXXX")"
trap 'rm -rf "$trusted_dir"' EXIT
trusted_runner="$trusted_dir/trusted_verify_review_state.sh"
git -C "$repo_root" cat-file blob HEAD:scripts/trusted_verify_review_state.sh > "$trusted_runner"
chmod 700 "$trusted_runner"
"$trusted_runner" --repo-root "$repo_root" --base "$base"
```

The `verification-gate` and repository-specific integrity workflow use the
same materialize-then-run path. The runner is a temporary repository-local
trust-root implementation; a future central Project Steward may replace the
launcher but must preserve the evidence schema, review-subject digest,
producer/role matrix, state model and merge-SHA binding.

Direct working-tree invocation remains useful only as defense in depth:

```text
python scripts/verify_review_state.py --check --base <canonical-40-hex-base>
```

It detects ordinary unstaged material deltas after it starts, but is explicitly
non-authoritative: a complete replacement of that working-tree Python file can
exit or perform side effects before it can inspect itself.

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
