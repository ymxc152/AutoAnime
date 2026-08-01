"""Operation batch DTO mapping."""

import json

from autoanime_v3.domain.entities import OperationBatchView, OperationItemView


class OperationRepository:
    def __init__(self, connection):
        self.connection = connection

    def get(self, batch_id):
        row = self.connection.execute(
            "SELECT * FROM operation_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:
            return None
        item_rows = self.connection.execute(
            "SELECT * FROM operation_items WHERE batch_id = ? ORDER BY sequence", (batch_id,)
        ).fetchall()
        items = tuple(
            OperationItemView(
                id=int(item["id"]),
                sequence=int(item["sequence"]),
                action=str(item["action"]),
                source_path=str(item["source_path"]),
                destination_path=str(item["destination_path"]),
                status=str(item["status"]),
                result_sha256=item["result_sha256"],
                error_code=item["error_code"],
                compensation_status=item["compensation_status"],
            )
            for item in item_rows
        )
        return OperationBatchView(
            id=int(row["id"]),
            plan_id=int(row["plan_id"]) if row["plan_id"] is not None else None,
            parent_batch_id=(
                int(row["parent_batch_id"]) if row["parent_batch_id"] is not None else None
            ),
            kind=str(row["kind"]),
            status=str(row["status"]),
            summary=json.loads(row["summary_json"] or "{}"),
            items=items,
        )

