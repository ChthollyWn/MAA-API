# SDD ledger — plan: docs/superpowers/plans/2026-09-25-m10-api-console.md

## Baseline and workspace
- Base: `07fef88` (`refactor/v2`); implementation branch: `m10-api-console`.
- Project requires direct checkout, no worktree. Working tree was clean before branch creation.
- Baseline `.venv/bin/python -m pytest -q` reached 100%; pytest.ini suppresses the normal passed-count summary under `-qq`; warnings are existing Starlette/httpx deprecations. Re-run at final gate for definitive exit evidence.
- Reachable authority: `docs/10`, `docs/12`, `docs/README`, `docs/13`; `docs/13 §8` contains M6–M9 notes only.

## Plan scan
| Task(s) | Shared file/interface | Scan result |
|---|---|---|
| 1 and 2 | `maa_api/main.py` app assembly; generated OpenAPI and TS types | Run sequentially; refresh generated contract after both backend tasks. |
| 1 and 3 | `request_id` HTTP header and WS `LogRecord` payload | Task 1 publishes exact optional field, then Task 3 consumes it. |
| 2 and 3 | Snippet CRUD paths and generated schemas | Task 2 creates API/schema first; Task 3 consumes generated types. |
| 4 and 1–3 | `docs/04/05/06/10/12/13`, README index | No code-file overlap; docs agent must use the confirmed decisions listed below. |
| 5 and all | Full branch verification, merge and tag | Runs after all implementation and review tasks. |

| Task | Self-consistency scan |
|---|---|
| 1 | Probe/test first, then middleware/LogHub/WS/CORS changes; no persistence migration for request_id. |
| 2 | Test-first model/API/migration; fields derive from docs/10 §2.5. |
| 3 | Frontend follows Task 1/2 contract; tests cover interactions and credential hygiene. |
| 4 | Documents existing and M10 behavior; REST/WS guide defers M11/M12 features. |
| 5 | Hard commands match AGENTS.md; final integration follows branch policy. |

## Confirmed decisions and implementation rulings
- User confirmed snippets are persistent CRUD; HTTP contract is GET/POST collection + GET/PUT/DELETE item, create 201, delete 204, missing 404, duplicate 409, shared error body.
- User confirmed name rule: trim, 1–64 chars, case-sensitive uniqueness.
- User confirmed JSON optional-field explanations are outside the valid JSON editor.
- User confirmed log linkage is request logs + `pipeline_id` continuation only; M11/M12 entity links are deferred.
- User confirmed phone acceptance is simulated viewport; device checks are supplemental; Tailscale remains M15.
- User confirmed guide covers implemented REST/WebSocket first and marks MCP pending M12.
- User confirmed remote Base URL switches a dedicated WebSocket; it must not alter global realtime state.
- User confirmed local history removes `Authorization`, `X-Token`, `Cookie`, and query `token`; replay uses current session auth.
- Ruling: snippet GET collection returns `{items,total}` ordered by `updated_at DESC, id DESC`; item is a typed view containing `id,name,method,path,path_params,query,headers,body,created_at,updated_at`. Errors use `API_SNIPPET_NOT_FOUND` (404) and `API_SNIPPET_NAME_CONFLICT` (409), following resource-scoped naming. Cost if wrong: public API name/ordering compatibility change.
- Ruling: sanitize credentials case-insensitively on backend writes as well as frontend serialization, removing standard auth headers and `query.token`; credentials are never persisted even for direct API callers. Cost if wrong: a custom header named as an app credential cannot be replayed from a snippet.
- Ruling: request_id is transient on in-memory LogRecord/WS only, not added to durable log schema. Cost if wrong: request trace cannot be recovered from persisted history after a server restart.
- Ruling: use Ajv-compatible OpenAPI JSON Schema validation and CodeMirror 6 JSON mode; body validation warns but never blocks sending.
- Ruling: credential header filtering additionally removes conventional `Proxy-Authorization`, `Set-Cookie`, API-key, password, and secret headers case-insensitively. Cost if wrong: a developer-authored header with one of these credential-like names cannot be saved for replay.
- Ruling: the guide generator takes status/code from `ErrorCode` + `ERROR_HTTP_STATUS`, Chinese meaning from docs/05, and tags from the live FastAPI OpenAPI metadata. This avoids duplicating error explanations in Python while keeping docs/14 generated and drift-checkable; the cost if wrong is that docs/05 remains a required generation dependency.
- Ruling: update legacy contract counts to 94 error codes / 93 HTTP-bound codes / 15 sections; `UPDATE_INTERRUPTED` remains the one non-HTTP error code. The old assertions were fixed counts and failed solely because M10 adds two surfaced API errors and section 4.15.
- Ruling: the migration backup regression now asserts the pre-0005 snapshot includes all pre-existing model tables and excludes `api_snippet`; current model metadata is expected to include the new table and cannot be compared wholesale to a pre-M10 backup.
- Ruling: background tasks created by update/device managers copy the current Context but clear only `current_request_id`; this prevents logs after the HTTP response being attributed to that request while preserving other context such as `current_pipeline_id`.
- Ruling: the console sends the log cursor as `first subscribe.data.last_seen_id`, not as WS URL query, so the server applies the current source/pipeline filter before backfill. The console reports `truncated` instead of silently hiding buffer gaps.
- Ruling: add CORS exposure of `Location` because POST `/api/snippets` returns it and browser integrations on allowed remote origins otherwise cannot read the advertised resource URI.
- Ruling: generated guide rows retain reserved code/status values but replace unshipped Confirmation/Agent/Tool/LLM details with a generic not-delivered note, matching the M10-only guide boundary.
- Ruling: the API console keeps mobile request selection in local component state instead of adding a Router entry per selection. `docs/10` and `docs/13 §8` document the resulting browser-back/deep-link tradeoff; `/more/api-console/guide` remains directly routable.
- Ruling: the existing log-scroll test could race initial auto-scroll; make it wait for the initial bottom pin before simulating a user scroll, keeping product code untouched.

## Task state
- Task 1: implementation `c0b1d3c`, review fix `2f9875e`, background Context isolation `5b4501b`; scoped re-reviews approved. Main agent final `.venv/bin/python -m pytest -q` exit 0.
- Task 2: implementation `4283446`, hardening `0ee8afd`, Location CORS `bb60741`; scoped reviews approved. Snippet/generator tests, OpenAPI snapshot, API smoke pass.
- Task 3: implementation `5bdf925`, review fixes `782e87e`, guide route `ce6d981`, WS cursor `14ea485`; review findings addressed. Final frontend gate: 13 files / 98 tests, typecheck, build all exit 0.
- Task 4: docs commits `65bfec9`, `36219ee`, guide completion `199eedb`, scope cleanup `8481932`; scoped docs review now updated against final guide. `generate_api_guide.py --check`, links and Bash examples pass.
- Task 5: after the no-ff integration into `refactor/v2`, reran the full acceptance chain: backend pytest, OpenAPI and guide checks, frontend install/gen/typecheck/test (13 files / 104 tests)/build, API smoke and frontend smoke all exit 0 / `SMOKE OK`.
- Supplemental hardware check not run: Codex CUA reports Mac GUI locked; `xcrun simctl` is not installed. ADB enumerated Samsung SM-G998B at `127.0.0.1:5555`, but no visible Android browser/screen check was performed. Tailscale remains M15.
- Scoped re-reviews: docs Task 4 approved; trace fix Task 1 approved; snippet hardening Task 2 approved; frontend Task 3 approved with no residual finding.
- Final frontend acceptance command was re-run as one chain: `pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`, exit 0 (13 files / 95 tests); build split `api-console`, `ajv`, and `JsonEditor` without the prior chunk warning.
- Final backend `.venv/bin/python -m pytest -q` exit 0; output summary is suppressed by repo pytest `-qq` config and showed 4 expected hardware skips. Existing Starlette/httpx deprecation warnings remain.
- `scripts/api_smoke.py`: 26/26, `SMOKE OK`; `scripts/frontend_smoke.py`: `SMOKE OK`; OpenAPI snapshot and generated-guide `--check` exit 0; seven affected documentation files' relative links all resolve.
- Integration: merge commit `434470750ad3422361c5fa802f3a8c0d362ea5df` is on `refactor/v2`; post-merge hard gate is green. Next create `v2-m10` at the final integration record commit and push branch plus tag.

## Final review follow-up (2026-09-25)
- Fresh Luna/max review found local-history/cURL custom credential header leaks (`X-Api-Key`, password, secret): added regression coverage first (3 RED cases) and aligned frontend filter with server credential-header matcher; focused utility tests now 19 passed.
- The same review found `/more/api-console/guide` inherited AppShell `max-w-3xl`; added shell test (RED) and gave both console and guide routes full width; layout tests 6 passed.
- Review found cURL always converted an explicit `X-Token` override into Bearer auth; added a RED regression test and preserved the chosen Authorization/X-Token channel while redacting the value by default. Focused utility tests 19 passed.
- Review found log sequence IDs may be reused after restart because live-only INFO events do not persist. Added process-scoped `stream_id` to WebSocket log records, replay batches, and subscription acknowledgements; mismatched stream cursors replay current memory and the client resets dedup state while warning about cross-instance gaps. Backend WebSocket module: 10 passed; focused realtime hook: 7 passed including restart regression.
- Matched the documented `ix_api_snippet_updated_at` against model and forward migration `0006`; model inventory and initial migration tests: 17 passed.
- Synced docs/04/05/06/10/12/13/14 for credential-header filtering, stream identity, actual schedule route/state prefill, M10 vs M14 boundaries, and added the real 800px tablet breakpoint test. Four focused front-end test files passed (43 tests); three backend WebSocket/model/migration files passed (all selected tests).
- User did not choose a separate body-scrubbing option and then directed merge/push; follow M10's explicit credential locations (auth headers + query token), retain JSON body unchanged for replay, and document that business secrets in body are persisted/exported.
- Current final evidence after all implementation fixes: OpenAPI snapshot generation, frontend API type generation, backend `.venv/bin/python -m pytest -q`, OpenAPI `--check`, guide `--check`, full frontend chain (13 files / 104 tests, typecheck and build), `scripts/api_smoke.py`, and `scripts/frontend_smoke.py` all exit 0 / `SMOKE OK`. One full-chain attempt found an omitted TS annotation for `log_batch.data.stream_id`; added the type and reran the frontend chain successfully. A prior backend run also hit the deliberately added legacy future-cursor regression and one existing supervisor timing assertion (150ms threshold); fixed the log cursor case, reran that supervisor test in isolation (pass), and the full backend suite then passed.
- Review also found the migration-backup test assumed the direct pre-head schema lacked the snippet table. With new head 0006, pre-head is 0005 and the table is present; reproduced the failure, then corrected the backup assertion to verify the table exists while the new index does not. Focused migration test now passes.
- A fresh final Luna/max review reported no remaining Critical/Important/Minor implementation findings after these fixes; it identified body-secret persistence as an explicit boundary to decide/document. Per the user's merge/push direction, we preserve JSON body and record the behavior in docs and ledger.
- Merged to `refactor/v2` using `git merge --no-ff`: `434470750ad3422361c5fa802f3a8c0d362ea5df`. The same hard gate was rerun on the merged checkout and passed; the final release tag/push follows the integration record commit.

## Rulings I made
- See confirmed implementation rulings above; all are implementation details inside user-approved behavior.
- Preserve the ordered custom auth channel in cURL exports (`Authorization` or `X-Token`) while replacing the auth value with `$MAA_TOKEN` by default; the explicit include-token control still exports the selected credential.
- Normalize header names with `.trim()` before credential detection and storage so whitespace variants cannot bypass header redaction; do not keep surrounding whitespace in saved request-header names.
- Add a process-scoped WS `stream_id` rather than persisting every ephemeral event ID. On an instance mismatch, replay current memory and show an incomplete-history notice; a future cursor without stream ID is treated as stale and also replays the current ring with `truncated=true`.
- Implement the documented snippet ordering index as a forward `0006` migration instead of editing already-created `0005`; verify backups from `0005` contain the snippet table but not the new index.
- Keep the three-column workbench at 1024px (`lg`) because the minimum columns exceed 800px tablet width; correct docs to the implemented `/more/schedules` navigation state and explicitly leave PWA install/offline/push acceptance in M14.
- Preserve arbitrary JSON request-body payloads because M10's enumerated credential locations are auth headers and query `token`; update docs/04, docs/10, docs/13 and docs/14 to state that JSON body secrets are not scrubbed and can persist/export.
- Browser screenshot and physical-device checks cannot be executed in this environment; record as outstanding supplemental checks and proceed because M10's required automated mobile/desktop viewport coverage passes and the hardware checks are explicitly non-gating.
