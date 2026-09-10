"""Bounded Schemoo chat documents; inference context and rows never enter here."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from threading import RLock

from psycopg.types.json import Jsonb
from schemii.common.api.errors import ApiProblem
from schemii.common.metadata.limit_events import LimitEventNotice
from schemii.common.metadata.users import ensure_local_metadata_user


class ConversationStore:
    def __init__(self, factory, policy, product):
        self.factory, self.policy, self.product = factory, policy, product
        self.lock = RLock()
        self.memory, self.preferences = {}, {}

    @contextmanager
    def session(self, owner):
        with self.lock:
            if self.factory is None:
                yield None
            else:
                with self.factory() as connection:
                    with connection.cursor() as cursor:
                        ensure_local_metadata_user(cursor, owner)
                        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"ai:{self.product}:{owner}",))
                        yield cursor

    def settings(self, owner, value=None):
        with self.session(owner) as cursor:
            if cursor is None:
                if value is not None: self.preferences[owner] = deepcopy(value)
                return deepcopy(self.preferences.get(owner, {}))
            if value is not None:
                cursor.execute("INSERT INTO metadata.ai_preferences VALUES (%s,%s,%s) ON CONFLICT(owner_id,product) DO UPDATE SET document=excluded.document", (owner,self.product,Jsonb(value)))
            cursor.execute("SELECT document FROM metadata.ai_preferences WHERE owner_id=%s AND product=%s", (owner,self.product))
            row = cursor.fetchone()
            return row["document"] if row else {}

    def _all(self, cursor, owner):
        if cursor is None: return deepcopy([v for (o,_),v in self.memory.items() if o == owner])
        cursor.execute("SELECT document FROM metadata.ai_conversations WHERE owner_id=%s AND product=%s ORDER BY updated_at DESC", (owner,self.product))
        return [row["document"] for row in cursor.fetchall()]

    def list(self, owner, subject=None):
        self.prune()
        with self.session(owner) as cursor:
            if cursor is not None:
                cursor.execute("SELECT document - 'messages' - 'activity' - 'pending' AS document FROM metadata.ai_conversations WHERE owner_id=%s AND product=%s AND (%s::text IS NULL OR subject_id=%s) ORDER BY updated_at DESC", (owner,self.product,subject,subject))
                return [row["document"] for row in cursor.fetchall()]
            return [{k:val for k,val in v.items() if k not in {"messages","activity","pending"}} for v in self._all(cursor,owner) if subject is None or v["modelId"] == subject]

    def _get(self, cursor, owner, chat_id):
        if cursor is None: value = deepcopy(self.memory.get((owner,chat_id)))
        else:
            cursor.execute("SELECT document FROM metadata.ai_conversations WHERE owner_id=%s AND product=%s AND id=%s", (owner,self.product,chat_id))
            row = cursor.fetchone(); value = row["document"] if row else None
        if value is None: raise ApiProblem(404,"ai_chat_not_found","This conversation is unavailable or has expired.")
        return value

    def get(self, owner, chat_id):
        with self.session(owner) as cursor: return self._get(cursor,owner,chat_id)

    def _save(self, cursor, owner, value):
        value["messages"] = value["messages"][-self.policy.message_history_limit:]
        value["activity"] = value["activity"][-self.policy.activity_history_limit:]
        limit = self.policy.context_bytes + self.policy.proposal_bytes_per_turn
        while len(json.dumps(value).encode()) > limit and len(value["messages"]) > 1:
            value["messages"].pop(0)
        while len(json.dumps(value).encode()) > limit and value["activity"]:
            value["activity"].pop(0)
        observed = len(json.dumps(value).encode())
        if observed > limit:
            raise ApiProblem(413,"ai_chat_size_limit","This conversation exceeds the configured chat size budget. Start a new chat or reduce the action batch.",
                limit_event=LimitEventNotice("schemoo_ai_chat", "ai.context_bytes + ai.proposal_bytes_per_turn", limit, observed))
        if cursor is None: self.memory[owner,value["id"]] = deepcopy(value)
        else:
            cursor.execute("INSERT INTO metadata.ai_conversations(id,owner_id,product,subject_id,document,updated_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET document=excluded.document,updated_at=excluded.updated_at", (value["id"],owner,self.product,value["modelId"],Jsonb(value),value["updatedAt"]))

    def create(self, owner, value):
        self.prune()
        with self.session(owner) as cursor:
            count = len(self._all(cursor,owner))
            if count >= self.policy.maximum_chats_per_workspace:
                raise ApiProblem(409,"ai_chat_limit","The configured conversation limit across your Schemoo models has been reached. Delete an old chat first.",
                    limit_event=LimitEventNotice("schemoo_ai_chat", "ai.maximum_chats_per_workspace", self.policy.maximum_chats_per_workspace, count))
            self._save(cursor,owner,value)
        return value

    def update(self, owner, chat_id, change, expected=None):
        with self.session(owner) as cursor:
            value = self._get(cursor,owner,chat_id)
            if expected is not None and value["revision"] != expected:
                raise ApiProblem(409,"ai_chat_revision_conflict","This chat changed. Refresh before trying again.")
            change(value)
            value["revision"] += 1
            value["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self._save(cursor,owner,value)
            return deepcopy(value)

    def delete(self, owner, chat_id):
        with self.session(owner) as cursor:
            self._get(cursor,owner,chat_id)
            if cursor is None: del self.memory[owner,chat_id]
            else: cursor.execute("DELETE FROM metadata.ai_conversations WHERE owner_id=%s AND product=%s AND id=%s", (owner,self.product,chat_id))

    def prune(self, recover=False):
        cutoff = (datetime.now(timezone.utc)-timedelta(days=self.policy.chat_retention_days)).isoformat()
        with self.lock:
            if self.factory is None:
                self.memory = {key:value for key,value in self.memory.items() if value["updatedAt"] >= cutoff}
                if recover:
                    for value in self.memory.values(): self._recover(value)
            else:
                with self.factory() as connection, connection.cursor() as cursor:
                    cursor.execute("DELETE FROM metadata.ai_conversations WHERE product=%s AND updated_at < %s", (self.product,cutoff))
                    if recover:
                        cursor.execute("SELECT owner_id,document FROM metadata.ai_conversations WHERE product=%s AND document->>'status'='working'", (self.product,))
                        for row in cursor.fetchall():
                            value=row["document"]; self._recover(value); self._save(cursor,row["owner_id"],value)

    @staticmethod
    def _recover(value):
        if value["status"] == "working":
            value.update(status="failed", pending=None, error="The server restarted during this turn. Inspect the model before retrying; actions are never replayed automatically.")
            value["revision"] += 1
