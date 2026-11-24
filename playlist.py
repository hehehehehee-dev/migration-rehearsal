"""Normalize playlist relationships and compare the resulting edge multiset."""
from collections import Counter

PLAYLIST_FAMILY = "playlist_membership"

SELECTS = {
    "copy_pairs": "SELECT PlaylistId, TrackId FROM PlaylistTrack",
    "reverse_insert_order": "SELECT PlaylistId, TrackId FROM PlaylistTrack ORDER BY PlaylistId DESC, TrackId DESC",
    "swap_equal_size_playlists": """SELECT CASE PlaylistId WHEN 13 THEN 14 WHEN 14 THEN 13 ELSE PlaylistId END,
                                    TrackId FROM PlaylistTrack""",
    "drop_one_pair": "SELECT PlaylistId, TrackId FROM PlaylistTrack ORDER BY PlaylistId, TrackId LIMIT -1 OFFSET 1",
    "duplicate_one_pair": "SELECT PlaylistId, TrackId FROM PlaylistTrack ORDER BY PlaylistId, TrackId LIMIT 1",
    "orphan_one_track": """SELECT PlaylistId, CASE WHEN (PlaylistId, TrackId) =
                           (SELECT PlaylistId, TrackId FROM PlaylistTrack ORDER BY PlaylistId, TrackId LIMIT 1)
                           THEN -1 ELSE TrackId END FROM PlaylistTrack""",
    "missing_unique_constraint": "SELECT PlaylistId, TrackId FROM PlaylistTrack",
}


def execute_membership_migration(db, mapping):
    has_unique_pair = mapping not in {"duplicate_one_pair", "missing_unique_constraint"}
    uniqueness = ", UNIQUE (PlaylistId, TrackId)" if has_unique_pair else ""
    db.execute(f"""CREATE TABLE PlaylistMembership (
        MembershipId INTEGER PRIMARY KEY,
        PlaylistId INTEGER NOT NULL REFERENCES Playlist(PlaylistId),
        TrackId INTEGER NOT NULL REFERENCES Track(TrackId)
        {uniqueness}
    )""")
    insert = "INSERT INTO PlaylistMembership (PlaylistId, TrackId) "
    if mapping == "duplicate_one_pair":
        db.execute(insert + SELECTS["copy_pairs"])
    db.execute(insert + SELECTS[mapping])


def membership_schema_matches(db, target):
    columns = [[row[1], row[2], row[3], row[5]] for row in db.execute("PRAGMA table_info(PlaylistMembership)")]
    foreign_keys = sorted([row[2], row[3], row[4]] for row in db.execute("PRAGMA foreign_key_list(PlaylistMembership)"))
    if columns != target["columns"] or foreign_keys != target["foreign_keys"]:
        return False
    for _, name, unique, _, partial in db.execute("PRAGMA index_list(PlaylistMembership)"):
        if unique and not partial:
            columns = [row[0] for row in db.execute("SELECT name FROM pragma_index_info(?) ORDER BY seqno", (name,))]
            if columns == target["unique_pair"]:
                return True
    return False


def verify_memberships(db, contract, baseline):
    """Use legacy pairs as the oracle. Surrogate IDs and insert order are irrelevant."""
    target = contract["target"]
    schema_matches = membership_schema_matches(db, target)
    issues = {} if schema_matches else {"membership_schema_mismatch": (1, [])}
    checks = {"membership_schema": schema_matches}
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='PlaylistMembership'").fetchone():
        return checks, issues

    expected_rows = baseline["PlaylistTrack"]
    actual_rows = db.execute(target["memberships_query"]).fetchall()
    expected, actual = Counter(expected_rows), Counter(actual_rows)
    missing, unexpected = expected - actual, actual - expected
    checks.update({
        "playlist_rows_expected": len(expected_rows), "playlist_rows_observed": len(actual_rows),
        "playlist_counts_exact": Counter(pair[0] for pair in expected_rows) == Counter(pair[0] for pair in actual_rows),
        "membership_pairs_exact": expected == actual,
        "memberships_verified": (expected & actual).total(),
        "missing_memberships": missing.total(), "unexpected_memberships": unexpected.total(),
    })
    names = dict(baseline["Playlist"])
    tracks = {row[0]: row[1] for row in baseline["Track"]}
    for code, delta in (("missing_memberships", missing), ("unexpected_memberships", unexpected)):
        if delta:
            examples = [
                {"playlist_id": pid, "playlist": names.get(pid), "track_id": tid, "track": tracks.get(tid),
                 "expected": expected[pid, tid], "observed": actual[pid, tid]}
                for pid, tid in sorted(delta)[:5]
            ]
            issues[code] = (delta.total(), examples)
    return checks, issues
