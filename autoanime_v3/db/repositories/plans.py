"""Plan DTO mapping helpers."""

import json
from pathlib import Path

from autoanime_v3.domain.entities import PlanItemView, PlanView


def plan_from_rows(plan_row, item_rows):
    items = []
    for row in item_rows:
        destination = str(Path(row["root_path"]) / row["destination_relative_path"])
        items.append(
            PlanItemView(
                id=int(row["id"]),
                source_location_id=int(row["source_location_id"]),
                source_path=str(row["source_path"]),
                destination_root_id=int(row["destination_root_id"]),
                destination_path=destination,
                destination_relative_path=str(row["destination_relative_path"]),
                action=str(row["action"]),
                reason=str(row["reason"] or ""),
                risk_level=str(row["risk_level"]),
                source_size=int(row["source_size"]),
                source_mtime_ns=int(row["source_mtime_ns"]),
                source_file_index=row["source_file_index"],
                source_sha256=row["source_sha256"],
                execution_status=str(row["execution_status"]),
                decision=row["decision"],
                reject_reason=row["reject_reason"],
                decided_by=int(row["decided_by"]) if row["decided_by"] is not None else None,
                decided_at=row["decided_at"],
            )
        )
    return PlanView(
        id=int(plan_row["id"]),
        scan_run_id=int(plan_row["scan_run_id"]),
        profile_id=int(plan_row["profile_id"]),
        profile_revision=int(plan_row["profile_revision"]),
        rule_version=str(plan_row["rule_version"]),
        library_revision=int(plan_row["library_revision"]),
        revision=int(plan_row["revision"]),
        status=str(plan_row["status"]),
        items=tuple(items),
        profile_snapshot=json.loads(plan_row["profile_snapshot_json"] or "{}"),
    )


class PlanRepository:
    def __init__(self, connection):
        self.connection = connection

    def get(self, plan_id):
        plan = self.connection.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if plan is None:
            return None
        items = self.connection.execute(
            """
            SELECT pi.*, fl.path AS source_path, sr.path AS root_path
            FROM plan_items pi
            JOIN file_locations fl ON fl.id = pi.source_location_id
            JOIN storage_roots sr ON sr.id = pi.destination_root_id
            WHERE pi.plan_id = ? ORDER BY pi.id
            """,
            (plan_id,),
        ).fetchall()
        return plan_from_rows(plan, items)

