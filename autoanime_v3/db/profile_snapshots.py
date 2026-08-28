"""Build immutable, JSON-serializable snapshots of scan profiles."""

import json
from datetime import datetime, timezone


def _row_dict(row):
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if mapping is not None else dict(row)


def _fetchone(connection, sql, parameters=()):
    if hasattr(connection, "exec_driver_sql"):
        return connection.exec_driver_sql(sql, parameters).mappings().first()
    return connection.execute(sql, parameters).fetchone()


def _fetchall(connection, sql, parameters=()):
    if hasattr(connection, "exec_driver_sql"):
        return connection.exec_driver_sql(sql, parameters).mappings().all()
    return connection.execute(sql, parameters).fetchall()


def build_profile_snapshot(connection, profile_id, profile_row=None, snapshot_at=None):
    profile = _row_dict(profile_row) or _row_dict(
        _fetchone(connection, "SELECT * FROM scan_profiles WHERE id = ?", (profile_id,))
    )
    if profile is None:
        raise KeyError(profile_id)
    source = _row_dict(
        _fetchone(connection, "SELECT * FROM storage_roots WHERE id = ?", (profile["source_root_id"],))
    )
    library = _row_dict(
        _fetchone(connection, "SELECT * FROM storage_roots WHERE id = ?", (profile["library_root_id"],))
    )
    rules_row = _row_dict(
        _fetchone(connection, "SELECT * FROM profile_rules WHERE profile_id = ?", (profile_id,))
    )
    rules = {}
    if rules_row is not None:
        rules = {
            key.removesuffix("_json"): json.loads(rules_row[key] or "[]")
            if key.endswith("_json")
            else int(rules_row[key])
            for key in (
                "include_globs_json",
                "exclude_globs_json",
                "media_extensions_json",
                "subtitle_extensions_json",
                "temporary_suffixes_json",
                "ignored_directories_json",
                "minimum_size",
            )
        }
    return {
        "profile_id": int(profile["id"]),
        "name": str(profile["name"]),
        "revision": int(profile["revision"]),
        "source_root_id": int(profile["source_root_id"]),
        "source_path": str(source["path"]) if source is not None else None,
        "library_root_id": int(profile["library_root_id"]),
        "library_path": str(library["path"]) if library is not None else None,
        "mode": str(profile["mode"]),
        "execution_policy": str(profile["execution_policy"]),
        "min_confidence": int(profile["min_confidence"]),
        "stability_seconds": int(profile["stability_seconds"]),
        "watch_enabled": bool(profile["watch_enabled"]),
        "enabled": bool(profile["enabled"]),
        "rules": rules,
        "snapshot_at": snapshot_at or datetime.now(timezone.utc).isoformat(),
    }


def encode_profile_snapshot(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
