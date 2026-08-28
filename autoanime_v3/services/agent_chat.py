"""Bound conversational agent sessions for review and library corrections."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, is_dataclass
from pathlib import Path

from autoanime_v3.db.engine import connect_sqlite
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.errors import InvalidStateError, NotFoundError, ValidationError
from autoanime_v3.services.changes import ChangeService
from autoanime_v3.services.corrections import CorrectionService
from autoanime_v3.services.memory import ShowMemoryService
from autoanime_v3.services.reviews import ReviewService


ALLOWED_PROPOSAL_FIELDS = {
    "title",
    "media_type",
    "season",
    "episode",
    "release_tag",
    "aliases",
    "reason",
}
FORBIDDEN_PROPOSAL_FIELDS = {
    "destination",
    "dest",
    "path",
    "action",
    "source",
    "destination_path",
    "destination_relative_path",
}
JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
INTERNAL_REASON = re.compile(
    r"(canonical_title|library_correction|normalized_key|media_type|title_locked|proposal_json)",
    re.IGNORECASE,
)
CHAT_ATTEMPTS = 3
AI_DISABLED = "__ai_disabled__"


def _json_safe(value):
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _parse_json_object(content):
    text = str(content or "").strip()
    fenced = JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return value if isinstance(value, dict) else None


def _sanitize_proposal(raw, kind):
    if not isinstance(raw, dict):
        return None
    candidate = dict(raw)
    if "title" not in candidate and candidate.get("title_zh"):
        candidate["title"] = candidate["title_zh"]
    proposal = {
        key: value
        for key, value in candidate.items()
        if key in ALLOWED_PROPOSAL_FIELDS and key not in FORBIDDEN_PROPOSAL_FIELDS
    }
    if isinstance(proposal.get("title"), str):
        proposal["title"] = proposal["title"].strip()
        if not proposal["title"]:
            proposal.pop("title")
    if isinstance(proposal.get("release_tag"), str):
        proposal["release_tag"] = proposal["release_tag"].strip()
    if "aliases" in proposal:
        aliases = proposal["aliases"]
        if isinstance(aliases, (list, tuple)):
            proposal["aliases"] = [str(item).strip() for item in aliases if str(item).strip()]
        else:
            proposal.pop("aliases")
    reason = str(proposal.get("reason") or "").strip()
    if not reason or len(reason) > 80 or INTERNAL_REASON.search(reason):
        proposal.pop("reason", None)
    valid = bool(proposal.get("title"))
    if kind == "review":
        valid = valid or proposal.get("season") is not None or proposal.get("episode") is not None
    return proposal if valid else None


def _assistant_content(proposal, raw_content):
    if raw_content == AI_DISABLED:
        return "AI 未启用。请先在设置中打开 AI 识别后再试。"
    if raw_content is None:
        return "连续 3 次未能连上 AI，请稍后重试。"
    if proposal is None:
        return "未生成可应用的提案，请再说明正确标题、季度或集号。"
    reason = str(proposal.get("reason") or "").strip()
    if reason and len(reason) <= 80 and not INTERNAL_REASON.search(reason):
        return reason
    title = str(proposal.get("title") or "").strip()
    if title:
        return f"准备将标题纠正为「{title}」。确认后点应用。"
    return "已根据你的说明生成提案，确认后可以应用。"


class AgentChatService:
    def __init__(self, database_path, chat_completion=None):
        self.database_path = Path(database_path)
        self.chat_completion = chat_completion
        run_migrations(self.database_path)

    def _connection(self):
        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        return connection

    def _session_row(self, connection, session_id):
        row = connection.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise NotFoundError("Agent session does not exist", {"id": session_id})
        return row

    def _session_view(self, connection, row):
        messages = []
        latest_proposal = None
        for message in connection.execute(
            "SELECT * FROM agent_messages WHERE session_id = ? ORDER BY id", (row["id"],)
        ).fetchall():
            proposal = json.loads(message["proposal_json"]) if message["proposal_json"] else None
            if message["role"] == "assistant" and proposal is not None:
                latest_proposal = proposal
            messages.append(
                {
                    "id": int(message["id"]),
                    "role": str(message["role"]),
                    "content": str(message["content"]),
                    "proposal": proposal,
                    "created_at": str(message["created_at"]),
                }
            )
        return {
            "id": int(row["id"]),
            "kind": str(row["kind"]),
            "target_id": int(row["target_id"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "proposal": latest_proposal,
            "messages": messages,
        }

    def _system_context(self, kind, target_id):
        if kind == "review":
            review = ReviewService(self.database_path).get(target_id)
            target = {
                "review_id": review.id,
                "review_type": review.review_type,
                "payload": review.payload,
            }
        elif kind == "library":
            connection = self._connection()
            try:
                row = connection.execute(
                    "SELECT id, canonical_title, revision, status FROM shows WHERE id = ?", (target_id,)
                ).fetchone()
                if row is None:
                    raise NotFoundError("Show does not exist", {"id": target_id})
                target = dict(row)
            finally:
                connection.close()
        else:
            raise ValidationError("Agent session kind must be review or library", {"kind": kind})
        aliases = ShowMemoryService(self.database_path).list()[:20]
        return (
            "你是绑定到当前对象的 AutoAnime 助手。只能提出 title、media_type、season、episode、"
            "release_tag、aliases、reason；绝不能提出 destination、path、action 或任何目标路径。"
            "对用户只用一两句中文，不要提及字段名、数据库或 JSON。"
            "reason 必须是给用户看的短句，例如「识别出错，标题应为测试」。"
            "同时给出一个 JSON 对象。上下文："
            + json.dumps({"kind": kind, "target": _json_safe(target), "learned_aliases": aliases}, ensure_ascii=False)
        )

    def open_session(self, kind: str, target_id: int) -> dict:
        kind = str(kind or "").strip()
        target_id = int(target_id)
        connection = self._connection()
        try:
            existing = connection.execute(
                "SELECT * FROM agent_sessions WHERE kind = ? AND target_id = ? AND status = 'open'",
                (kind, target_id),
            ).fetchone()
            if existing is not None:
                return self._session_view(connection, existing)
        finally:
            connection.close()

        system_content = self._system_context(kind, target_id)
        with SqliteUnitOfWork(self.database_path) as uow:
            existing = uow.connection.execute(
                "SELECT * FROM agent_sessions WHERE kind = ? AND target_id = ? AND status = 'open'",
                (kind, target_id),
            ).fetchone()
            if existing is None:
                cursor = uow.connection.execute(
                    "INSERT INTO agent_sessions(kind, target_id, status) VALUES (?, ?, 'open')",
                    (kind, target_id),
                )
                session_id = int(cursor.lastrowid)
                uow.connection.execute(
                    "INSERT INTO agent_messages(session_id, role, content) VALUES (?, 'system', ?)",
                    (session_id, system_content),
                )
            else:
                session_id = int(existing["id"])
            uow.commit()
        return self.get(session_id)

    def get(self, session_id: int) -> dict:
        connection = self._connection()
        try:
            return self._session_view(connection, self._session_row(connection, int(session_id)))
        finally:
            connection.close()

    def _chat_once(self, config, messages):
        endpoint = str(config["openai_base_url"]).rstrip("/")
        if not endpoint.endswith("/v1/chat/completions"):
            endpoint += "/v1/chat/completions"
        body = {
            "model": config["openai_model"],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + config["openai_api_key"],
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=config["openai_timeout"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return str(payload["choices"][0]["message"]["content"])
        except (OSError, KeyError, IndexError, TypeError, ValueError, urllib.error.URLError):
            return None

    def _default_chat_completion(self, messages):
        from autoanime_v3.services.scans import CoreScanAdapter

        config = CoreScanAdapter(self.database_path)._openai_config()
        if not config["openai_enabled"]:
            return None
        return self._chat_once(config, messages)

    def _complete_with_retry(self, messages, kind):
        if self.chat_completion is not None:
            raw_content = self.chat_completion(messages)
            return raw_content, _sanitize_proposal(_parse_json_object(raw_content), kind)

        from autoanime_v3.services.scans import CoreScanAdapter

        config = CoreScanAdapter(self.database_path)._openai_config()
        if not config["openai_enabled"]:
            return AI_DISABLED, None
        last_raw = None
        for _ in range(CHAT_ATTEMPTS):
            last_raw = self._chat_once(config, messages)
            if last_raw is None:
                continue
            proposal = _sanitize_proposal(_parse_json_object(last_raw), kind)
            if proposal is not None:
                return last_raw, proposal
        return last_raw, _sanitize_proposal(_parse_json_object(last_raw), kind) if last_raw else None

    def add_message(self, session_id: int, content: str) -> dict:
        session_id = int(session_id)
        text = str(content or "").strip()
        if not text:
            raise ValidationError("Message content is required")
        with SqliteUnitOfWork(self.database_path) as uow:
            row = self._session_row(uow.connection, session_id)
            if row["status"] != "open":
                raise InvalidStateError("Agent session is not open")
            uow.connection.execute(
                "INSERT INTO agent_messages(session_id, role, content) VALUES (?, 'user', ?)",
                (session_id, text),
            )
            uow.connection.execute(
                "UPDATE agent_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,)
            )
            uow.commit()

        current = self.get(session_id)
        llm_messages = [
            {"role": item["role"], "content": item["content"]}
            for item in current["messages"]
            if item["role"] in {"system", "user", "assistant"}
        ]
        raw_content, proposal = self._complete_with_retry(llm_messages, current["kind"])
        assistant_content = _assistant_content(proposal, raw_content)

        with SqliteUnitOfWork(self.database_path) as uow:
            row = self._session_row(uow.connection, session_id)
            if row["status"] != "open":
                raise InvalidStateError("Agent session is not open")
            uow.connection.execute(
                "INSERT INTO agent_messages(session_id, role, content, proposal_json) VALUES (?, 'assistant', ?, ?)",
                (
                    session_id,
                    assistant_content,
                    json.dumps(proposal, ensure_ascii=False, sort_keys=True) if proposal is not None else None,
                ),
            )
            uow.connection.execute(
                "UPDATE agent_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,)
            )
            uow.commit()
        return self.get(session_id)

    def apply(self, session_id: int, user_id=None) -> dict:
        session = self.get(int(session_id))
        if session["status"] != "open":
            raise InvalidStateError("Agent session is not open")
        proposal = session["proposal"]
        if proposal is None:
            raise ValidationError("Agent session has no proposal to apply")

        if session["kind"] == "review":
            resolution = {
                key: proposal[key]
                for key in ("title", "media_type", "season", "episode", "release_tag")
                if key in proposal
            }
            resolution["manual_lock"] = True
            result = ReviewService(self.database_path).resolve(session["target_id"], resolution, user_id)
        else:
            title = str(proposal.get("title") or "").strip()
            if not title:
                raise ValidationError("A title is required for a library correction")
            connection = self._connection()
            try:
                show = connection.execute(
                    "SELECT revision FROM shows WHERE id = ?", (session["target_id"],)
                ).fetchone()
                if show is None:
                    raise NotFoundError("Show does not exist", {"id": session["target_id"]})
                revision = int(show["revision"])
            finally:
                connection.close()
            request = ChangeService(self.database_path).preview_show_change(
                session["target_id"],
                revision,
                {"canonical_title": title, "title_locked": True},
                str(proposal.get("reason") or "识别出错"),
            )
            result = CorrectionService(self.database_path).apply(request.id, user_id)

        with SqliteUnitOfWork(self.database_path) as uow:
            row = self._session_row(uow.connection, int(session_id))
            if row["status"] != "open":
                raise InvalidStateError("Agent session is not open")
            uow.connection.execute(
                "UPDATE agent_sessions SET status = 'applied', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(session_id),),
            )
            uow.commit()
        response = self.get(int(session_id))
        response.update({"applied": True, "result": _json_safe(result)})
        return response

    def abandon(self, session_id: int) -> dict:
        with SqliteUnitOfWork(self.database_path) as uow:
            row = self._session_row(uow.connection, int(session_id))
            if row["status"] != "open":
                raise InvalidStateError("Agent session is not open")
            uow.connection.execute(
                "UPDATE agent_sessions SET status = 'abandoned', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(session_id),),
            )
            uow.commit()
        return self.get(int(session_id))
