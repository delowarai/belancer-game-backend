# Step 2 — game rules, time and ranked validation

Rules version 2 has separate daily challenge identifiers and leaderboards. Old game sessions and private history are preserved. Run `python -m alembic upgrade head` before starting the upgraded API. Revision 0002 adds immutable daily challenge content; no user/history data is deleted.

## Rules

- Memory Grid practice: 4×4, three cells shown for two seconds. Increase cell count after every two successful rounds. End after three failures or ten rounds. Ten points per correct cell. A 180-second ceiling prevents stuck sessions.
- Memory Grid ranked: ten three-cell rounds, 180-second limit. Rank by completely correct rounds, incorrect cells, answer response time (two-second display excluded), then submission timestamp.
- Pair Finder: eight pairs, 180-second limit. Count only pairs actually found. Rank completed games by duration then moves; unfinished games by pairs found then moves.
- Quick Match and Math Sprint: 60 seconds, maximum 120 questions. 100 points per correct answer; record errors separately. Rank score, accuracy, submission timestamp.
- Focus Finder: ten questions in 120 seconds. 100 points per correct answer, same ranking as timed games.
- Three ranked starts per daily game; UTC boundaries. All players/attempts share stored content for that daily game. Repeated attempts may become familiar.

## API

`POST /api/sessions` returns the current prompt, server time, deadline and next action sequence. It never returns the whole puzzle. `POST /api/sessions/{id}/actions` accepts `{sequence, answer}`. A repeated identical sequence is replay-safe and returns current state; a changed answer or skipped sequence is rejected. Pair reveals are server-owned, with enforced hide delays. Memory answers are blocked while the pattern is displayed. Hidden patterns are not reissued on refresh or retry.

`GET /api/sessions/{id}` resumes the same user's session without consuming another attempt. `POST /api/sessions/{id}/finish` with `{}` finalizes a timed-out partial result, or retrieves an already finalized result. New sessions reject the old bulk-submit endpoint.

The frontend has a three-second countdown, wall-clock timer, same-browser refresh recovery, inline answer feedback, retries, and replay. Leaving the tab never pauses a ranked timer. If a request fails, retry the same action or reload current session state.

## Limits

This reduces payload inspection and simple score tampering; it does not make a browser game bot-proof. The current round is visible to the browser. Network latency affects response timing and throughput. There is no device attestation, suspicion classifier, endpoint throttling, scheduled expiry worker, or global session discovery across devices yet. Expired abandoned sessions produce no result until resumed/finalized; they still consume attempts.

PostgreSQL provides row locks; a comparison against the previously stored payload prevents stale writes in SQLite. Concurrent request conflicts require reloading/retrying. PostgreSQL live concurrency remains to be tested. Daily challenges currently publish on first use rather than through an admin scheduler.

Step 1 local setup changes are included. Full admin management is the next milestone.
