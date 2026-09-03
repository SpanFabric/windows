#!/usr/bin/env bash
# Authoritative Manual Verification Bridge v1 pre-execution launcher.
# This file must itself be materialized from a Git commit object before use.
set -euo pipefail

# A normal Git-Bash session already includes /usr/bin. Add the host Bash
# directory explicitly as well so a host invoking Git's bash.exe directly
# still has mktemp, env and rm without consulting the repository worktree.
if [[ -n "${BASH:-}" ]]; then
  PATH="${BASH%/*}:$PATH"
  export PATH
fi

die() {
  printf 'TRUSTED_PREFLIGHT_REJECTED: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat >&2 <<'EOF'
Usage: trusted_verify_review_state.sh --repo-root PATH --base CANONICAL_SHA

Run only a copy materialized from HEAD:scripts/trusted_verify_review_state.sh.
EOF
  exit 2
}

repo_root=''
base=''
base_supplied=0
while (($#)); do
  case "$1" in
    --repo-root)
      (($# >= 2)) || usage
      repo_root="$2"
      shift 2
      ;;
    --base)
      (($# >= 2)) || usage
      base="$2"
      base_supplied=1
      shift 2
      ;;
    --help|-h)
      usage
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$repo_root" ]] || die '--repo-root is required'
((base_supplied)) || die 'authoritative execution requires an explicit --base SHA'
[[ -d "$repo_root" ]] || die "--repo-root is not a directory: $repo_root"
[[ -f "$0" ]] || die 'runner must be materialized as a regular temporary file, not stdin or a working-tree path'
repo_root="$(cd "$repo_root" && pwd -P)"

# Git environment overrides can redirect the repository/index/object database.
# The host shell supplies the executable search path; all repository selection
# below is explicit and receives the current root as its safe-directory value.
git_repo() {
  env \
    -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
    -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
    -u GIT_CONFIG -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM \
    -u GIT_CONFIG_NOSYSTEM -u GIT_CONFIG_COUNT \
    -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 \
    git -c "safe.directory=$repo_root" -C "$repo_root" "$@"
}

is_non_material() {
  [[ "$1" == 'verification/state.yaml' || "$1" == verification/reports/* ]]
}

reject_material_paths() {
  local kind="$1"
  local input="$2"
  local path
  while IFS= read -r -d '' path; do
    if is_non_material "$path"; then
      continue
    fi
    die "$kind material path: $path"
  done < "$input"
}

tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/spangpu-trusted-verify.XXXXXX")" || die 'unable to create isolated temporary directory'
cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT HUP INT TERM
umask 077

# Pre-execution boundary: no repository Python has run before these NUL-safe
# Git plumbing checks complete. Only the two fixed non-material exclusions are
# allowed; literal-backslash and other lookalikes never match these patterns.
git_repo diff-files --no-ext-diff --name-only -z -- > "$tmp_root/unstaged.paths" || die 'cannot inspect tracked working-tree deltas'
reject_material_paths 'unstaged tracked delta' "$tmp_root/unstaged.paths"
git_repo ls-files --others --exclude-standard -z -- > "$tmp_root/untracked.paths" || die 'cannot inspect untracked working-tree paths'
reject_material_paths 'untracked' "$tmp_root/untracked.paths"

commit="$(git_repo rev-parse --verify --quiet 'HEAD^{commit}')" || die 'HEAD must resolve to a commit before trusted execution'
runner_oid="$(git_repo rev-parse "$commit:scripts/trusted_verify_review_state.sh")" || die 'trusted runner blob is absent from HEAD'
runner_actual="$(git_repo hash-object --stdin < "$0")" || die 'cannot hash materialized runner copy'
[[ "$runner_actual" == "$runner_oid" ]] || die "materialized runner hash does not match HEAD blob ($runner_actual != $runner_oid)"

validator_oid="$(git_repo rev-parse "$commit:scripts/verify_review_state.py")" || die 'validator blob is absent from HEAD'
validator_copy="$tmp_root/verify_review_state.py"
git_repo cat-file blob "$validator_oid" > "$validator_copy" || die 'cannot materialize validator blob from HEAD'
validator_actual="$(git_repo hash-object --stdin < "$validator_copy")" || die 'cannot hash materialized validator copy'
[[ "$validator_actual" == "$validator_oid" ]] || die "materialized validator hash does not match HEAD blob ($validator_actual != $validator_oid)"
chmod 700 "$validator_copy"

# The validator runs from the private temp directory with isolated Python
# imports. It receives the repository explicitly and never imports working-tree
# Python before the preflight has accepted the checkout.
(
  cd "$tmp_root"
  env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 python -I "$validator_copy" \
    --repo-root "$repo_root" --check --base "$base"
)

printf 'TRUSTED_RUNNER_GIT_OBJECT=%s\n' "$runner_oid"
printf 'TRUSTED_VALIDATOR_GIT_OBJECT=%s\n' "$validator_oid"
printf 'TRUSTED_VALIDATOR_COPY_OBJECT=%s\n' "$validator_actual"
