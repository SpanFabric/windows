# SHARED-FB-001 — Builder handoff, not independent acceptance

Root cause: Only the final state transition was checked; base history established risk/ancestry but not reachability. A later legal event concealed a committed illegal reopen.

Model: complete durable DAG from genuine root, per-commit policy snapshots, exact two-parent ancestor snapshot adoption, and one current HEAD successor. Missing/shallow/unknown/ambiguous histories fail closed. See verification/MANUAL_AUTHORITY.md. Five material paths are identical across all six. Existing first-push workflow remains unchanged.

Current repository: windows
Material SHA: d77c5e0fe5661a0affa6be7e19cda2c470edc679
Review-Subject-Digest: ba7c9f3389e5255fa765c668a0def6fef626c93820bdae92001f21fbd9797c86

All six material subjects:
- platform: b72785fb18b58f929bdd84688bbebeb6dd1b8142 / d1f947276d98e8e92811f826b2c440d3be09bd88b45506eeba57f3c9f01f06dc
- windows: d77c5e0fe5661a0affa6be7e19cda2c470edc679 / ba7c9f3389e5255fa765c668a0def6fef626c93820bdae92001f21fbd9797c86
- host: 5533d0ea6cb0771618f7f5f9c3cdd44a8c025436 / 9b7e7a40a04c2293ab454d408854cf9ff82051ad3e02f47fed5b8e0dc94a45ad
- evals: 3d706158ca4fa3486d99af0462b0b5653ff1e333 / af7e63910d24fb507f86d52553adbfb15a7344a1c0dd778e8337bd8c5f5bbded
- docs: 25fc8f634e896fb7ba08a287cfe7efaa4c1c842b / f242168b280d07b70d27d86e6cd940217a713815e5f3f4cbd4c57cebc72e5567
- orggithub: 174b3f711a34ec02351b20a9a508c1fa3fd95d24 / 7cac1cf98c2d03c0313ada58d62e23d5d5647f5497830cf1f64352829f45d5de

Fresh BREAKER must refetch the exact updated PR heads, reconstruct requirements independently, distrust Builder green, and rerun durable wrong-role/metadata/material/skipped-commit/policy-version/merge/shallow/root/stale-evidence attacks. Attack base=HEAD truncation, missing objects, replacement/graft history, unsupported merges and policy weakening. Verify the old feature-push fix, Windows exact-success prerequisite, full history fetch and exact-head required contexts. No feature-push Verification gate run is expected. The final batch handoff records concrete PR heads and CI run IDs after normal push.

All prior evidence remains historical and inactive. SHARED-FB-001 stays OPEN/REMEDIATED_PENDING_FRESH_BREAKER. Maximum lifecycle INDEPENDENT_REVIEW_PENDING. No Fresh BREAKER, Owner Acceptance, merge, phase authorization or gate result is issued. UP-003 and old persistence PRs/branches/reports are untouched. The known unsupported divergent merge case must remain blocked; do not quietly choose a parent or infer authority.
