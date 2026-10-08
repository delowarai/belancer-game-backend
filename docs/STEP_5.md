# Step 5: period leaderboards

Pull backend master and frontend main and restart both servers. No new schema migration is required; revision 0003 remains the prerequisite.

The frontend uses `/api/standings/{game}` with daily, weekly and all-time periods, UTC date selection, configuration profiles and pagination (20 players per page). Public visitors can view standings; logged-in visitors see their rank even when their row is on another page. The existing daily `/api/leaderboards/{game}` endpoint is retained for compatibility.

Daily uses the selected challenge date. Weekly uses Monday through Sunday of the selected week, excluding future days. All-time includes recorded challenges through the selected date. Each board selects one best validated attempt per active player, using the existing game-specific ranking order. These are best-attempt boards, not accumulated points or weekly participation awards. Manual flags alone do not remove results; invalidated results and suspended accounts are excluded.

Only rules v2 are included. Frozen duration, round and cell configuration separates boards. The default profile follows the selected date's challenge snapshot, or current configured defaults if no challenge exists. Other profiles with validated results in that period can be selected. Session snapshots supply configuration, with challenge/default fallback for older v2 records. Matching settings do not guarantee equal puzzle difficulty across days, so the UI explains this limitation. Scores are not compared across different games/configuration profiles. No prizes or claims of competitive difficulty calibration are included.

Public responses omit moderation notes, reviewer identities, account IDs and session payloads. Sorting uses stable account IDs internally to resolve an otherwise identical tie. Offset pagination counts players after best-attempt selection; displayed ranks remain global within the selected board.

## Validation and limits

70 backend integration tests passed, including period boundaries, historical dates, future-date rejection, best-attempt selection, configuration/version separation, all five game-specific rank orders, moderation/suspension, privacy, own rank and pagination. Six frontend game tests and the TypeScript/Vite production build passed. Browser checks in a separate synthetic local database covered daily/weekly/all-time switching and a real stored validated result. Screenshot saved as a local delivery artifact.

The API scans stored results and performs ranking in Python, like the existing daily API. Larger installations need normalized indexed results, database aggregation, bounded queries and caching. PostgreSQL load/concurrency and a full mobile walkthrough are not verified. Empty historical dates without a challenge use today's configured defaults for their default profile; other recorded profiles remain selectable.
