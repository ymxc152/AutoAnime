"""Review item DTO mapping."""

import json

from autoanime_v3.domain.entities import ReviewItemView


def review_from_row(row):
    return ReviewItemView(
        id=int(row["id"]),
        scan_run_id=int(row["scan_run_id"]),
        media_file_id=int(row["media_file_id"]) if row["media_file_id"] is not None else None,
        review_type=str(row["review_type"]),
        status=str(row["status"]),
        payload=json.loads(row["payload_json"] or "{}"),
        resolution=json.loads(row["resolution_json"]) if row["resolution_json"] else None,
    )

