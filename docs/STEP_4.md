# Step 4: account settings and personal progress

Pull backend master and frontend main and restart both servers. No schema change is introduced; databases must already be upgraded through revision 0003.

## Account settings

Open `/settings` from the player dashboard. Username changes require the current password, preserve account ID/history/role, normalize to lowercase, and reject existing names. The public leaderboard name changes with the account.

Password changes require the current password and a different new password of 8–128 characters; the UI confirms the new password. All login sessions are revoked and the browser returns to login. Existing hashing and same-origin/CSRF protections apply. Username/password changes are audited without passwords or hashes in audit details. Sign out everywhere revokes only the player's sessions.

Email recovery remains unavailable because no email service or verified email ownership exists in this deployment. Account deletion and email authentication are not implemented.

## Progress

`/dashboard` and `/progress` show personal ranked starts, submitted attempts (including partial/invalidated results), active UTC days and current streak. Active days require a validated result; the current streak ends today or yesterday. Invalidated results do not count toward accuracy, completions or streaks. Manual flags alone do not invalidate a result.

Per-game rows show starts, validated submissions, completed attempts and accuracy weighted by correct/incorrect answers. Activity can be filtered by game, with 20 rows per page. Only the signed-in player's data appears; moderation reasons/reviewer identities are omitted. Practice results remain local. Scores across games, rule versions or configuration changes are not compared as personal bests.

The API reads the player's stored sessions to compute aggregates; larger installations need normalized/indexed result fields and database aggregation. Activity dates display browser local time; streaks always use UTC. No health or cognitive improvement claims are made.

## Validation

56 backend tests and six frontend engine tests passed; TypeScript/Vite build passed. New tests cover password verification, duplicate names, stable identity, role injection rejection, password/session revocation, other-user isolation, progress filtering/pagination, partial/invalidated results, moderation-note redaction and UTC streaks. Browser checks used a separate synthetic database: progress/history, empty game filter, settings rendering and sign out everywhere. Password mutation was checked through integration tests, not a real user's browser account. PostgreSQL concurrent account updates and mobile settings interactions remain unverified.
