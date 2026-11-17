"""Isolated first-release foundation. No real identity, payment or audio processing."""

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone


TRACKS = {"development", "support"}
PUBLIC_SCENARIO = {"scenarioId", "version", "track", "unitId", "title", "publicBrief", "learningObjective"}
PUBLIC_VARIANT = {"variantId", "level", "minTurns", "maxTurns", "hintPolicy"}
PUBLIC_DAY = {"dayId", "courseDay", "track", "unitId", "microSpeakingTask", "scenarioRef", "corePhraseIds"}


class DomainError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path=":memory:"):
        # HTTPServer handles one request at a time; test harness runs it in a worker thread.
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, adult INTEGER NOT NULL DEFAULT 0,
          consent_version TEXT, track TEXT, goal_minutes INTEGER,
          assessment TEXT NOT NULL DEFAULT 'UNTESTED', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tokens (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS content (
          kind TEXT NOT NULL, content_id TEXT NOT NULL, version TEXT NOT NULL,
          object_hash TEXT NOT NULL, object_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'DRAFT', release_allowed INTEGER NOT NULL,
          editor_id TEXT NOT NULL, reviewer_id TEXT,
          created_at TEXT NOT NULL,
          PRIMARY KEY(kind, content_id, version));
        CREATE TABLE IF NOT EXISTS enrollments (
          user_id TEXT PRIMARY KEY REFERENCES users(id), track TEXT NOT NULL,
          next_day INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'ACTIVE');
        CREATE TABLE IF NOT EXISTS completions (
          user_id TEXT NOT NULL REFERENCES users(id), course_day INTEGER NOT NULL,
          day_id TEXT NOT NULL, content_version TEXT NOT NULL,
          evidence_id TEXT NOT NULL, completed_at TEXT NOT NULL,
          PRIMARY KEY(user_id, course_day), UNIQUE(user_id, evidence_id));
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL,
          action TEXT NOT NULL, target TEXT NOT NULL, at TEXT NOT NULL);
        """)

    def audit(self, actor, action, target):
        self.db.execute("INSERT INTO audit(actor_id,action,target,at) VALUES(?,?,?,?)", (actor, action, target, now()))

    def login_sandbox(self, subject):
        if not isinstance(subject, str) or not subject.startswith("fixture:") or len(subject) > 100:
            raise DomainError("fixture_identity_required")
        self.db.execute("INSERT OR IGNORE INTO users(id,created_at) VALUES(?,?)", (subject, now()))
        token = secrets.token_urlsafe(32)
        expiry = int(datetime.now(timezone.utc).timestamp()) + 3600
        self.db.execute("INSERT INTO tokens VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), subject, expiry))
        return {"token": token, "expiresIn": 3600}

    def user_for_token(self, token):
        if not token:
            raise DomainError("unauthorized", 401)
        row = self.db.execute("SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.expires_at>?", (hashlib.sha256(token.encode()).hexdigest(), int(datetime.now(timezone.utc).timestamp()))).fetchone()
        if not row:
            raise DomainError("unauthorized", 401)
        return dict(row)

    def onboard(self, user_id, adult, consent_version, track, goal_minutes):
        if adult is not True:
            raise DomainError("adult_required", 403)
        if consent_version != "sandbox-v1":
            raise DomainError("consent_required", 403)
        if track not in TRACKS or goal_minutes not in (3, 5, 10, 15):
            raise DomainError("invalid_plan")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE users SET adult=1,consent_version=?,track=?,goal_minutes=? WHERE id=?", (consent_version, track, goal_minutes, user_id))
            old = self.db.execute("SELECT track FROM enrollments WHERE user_id=?", (user_id,)).fetchone()
            if old and old["track"] != track:
                raise DomainError("track_change_requires_review", 409)
            self.db.execute("INSERT OR IGNORE INTO enrollments(user_id,track) VALUES(?,?)", (user_id, track))
            self.audit(user_id, "ONBOARD", track)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return self.profile(user_id)

    def profile(self, user_id):
        row = self.db.execute("SELECT id,adult,consent_version,track,goal_minutes,assessment FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row)

    def require_training(self, user_id):
        user = self.db.execute("SELECT adult,consent_version,track FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not user["adult"] or user["consent_version"] != "sandbox-v1" or user["track"] not in TRACKS:
            raise DomainError("training_not_allowed", 403)
        return user

    def withdraw(self, user_id):
        self.db.execute("UPDATE users SET consent_version=NULL WHERE id=?", (user_id,))
        self.audit(user_id, "WITHDRAW_CONSENT", user_id)
        return {"status": "WITHDRAWN"}

    def import_content(self, actor, kind, obj):
        if kind not in ("scenario", "day", "rubric", "exam") or not isinstance(obj, dict):
            raise DomainError("invalid_content")
        id_key = {"scenario": "scenarioId", "day": "dayId", "rubric": "rubricId", "exam": "examFormId"}[kind]
        content_id, version = obj.get(id_key), obj.get("version") or obj.get("contentVersion")
        if not isinstance(content_id, str) or not content_id or not isinstance(version, str) or not version:
            raise DomainError("content_id_version_required")
        if kind == "scenario" and (not isinstance(obj.get("privateFacts", []), list) or obj.get("track") not in TRACKS | {"common"}):
            raise DomainError("invalid_scenario")
        if kind == "day" and (obj.get("track") not in TRACKS or not isinstance(obj.get("courseDay"), int) or not 1 <= obj["courseDay"] <= 30):
            raise DomainError("invalid_day")
        object_hash = digest(obj)
        existing = self.db.execute("SELECT object_hash FROM content WHERE kind=? AND content_id=? AND version=?", (kind, content_id, version)).fetchone()
        if existing:
            if existing["object_hash"] != object_hash:
                raise DomainError("immutable_version_conflict", 409)
            return {"id": content_id, "version": version, "sha256": object_hash, "idempotent": True}
        self.db.execute("INSERT INTO content VALUES(?,?,?,?,?,'DRAFT',?,?,NULL,?)", (kind, content_id, version, object_hash, canonical(obj), int(obj.get("releaseAllowed") is True), actor, now()))
        self.audit(actor, "IMPORT_DRAFT", f"{kind}:{content_id}:{version}")
        return {"id": content_id, "version": version, "sha256": object_hash, "idempotent": False}

    def review_content(self, actor, kind, content_id, version):
        row = self.db.execute("SELECT editor_id,release_allowed,status FROM content WHERE kind=? AND content_id=? AND version=?", (kind, content_id, version)).fetchone()
        if not row:
            raise DomainError("content_not_found", 404)
        if actor == row["editor_id"]:
            raise DomainError("self_review_forbidden", 403)
        if not row["release_allowed"]:
            raise DomainError("draft_not_release_allowed", 409)
        if row["status"] != "DRAFT":
            raise DomainError("invalid_review_state", 409)
        self.db.execute("UPDATE content SET status='PUBLISHED',reviewer_id=? WHERE kind=? AND content_id=? AND version=?", (actor, kind, content_id, version))
        self.audit(actor, "PUBLISH", f"{kind}:{content_id}:{version}")
        return {"status": "PUBLISHED"}

    def public_content(self, user_id, kind, content_id, version):
        self.require_training(user_id)
        row = self.db.execute("SELECT object_json,status FROM content WHERE kind=? AND content_id=? AND version=?", (kind, content_id, version)).fetchone()
        if not row or row["status"] != "PUBLISHED":
            raise DomainError("content_not_available", 404)
        obj = json.loads(row["object_json"])
        allowed = PUBLIC_SCENARIO if kind == "scenario" else PUBLIC_DAY if kind == "day" else set()
        public = {key: obj[key] for key in allowed if key in obj}
        if kind == "scenario":
            public["variants"] = [{key: item[key] for key in PUBLIC_VARIANT if key in item} for item in obj.get("variants", []) if isinstance(item, dict)]
        if kind == "day" and isinstance(public.get("scenarioRef"), dict):
            public["scenarioRef"] = {key: public["scenarioRef"][key] for key in ("scenarioId", "variantId", "version") if key in public["scenarioRef"]}
        return public

    def course(self, user_id):
        user = self.require_training(user_id)
        row = self.db.execute("SELECT next_day,status FROM enrollments WHERE user_id=?", (user_id,)).fetchone()
        day = row["next_day"] if row else 1
        candidates = self.db.execute("SELECT object_json FROM content WHERE kind='day' AND status='PUBLISHED'").fetchall()
        items = [json.loads(x["object_json"]) for x in candidates]
        matching = [x for x in items if x.get("track") == user["track"] and x.get("courseDay") == day]
        public = {key: matching[0][key] for key in PUBLIC_DAY if key in matching[0]} if len(matching) == 1 else None
        return {"nextDay": day, "status": row["status"] if row else "ACTIVE", "day": public, "assessment": "UNTESTED"}

    def complete_day(self, user_id, evidence_id):
        self.require_training(user_id)
        if not isinstance(evidence_id, str) or not evidence_id.startswith("fixture-evidence:"):
            raise DomainError("effective_evidence_required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute("SELECT course_day FROM completions WHERE user_id=? AND evidence_id=?", (user_id, evidence_id)).fetchone()
            if existing:
                self.db.execute("COMMIT")
                return {"completedDay": existing["course_day"], "idempotent": True}
            enrollment = self.db.execute("SELECT next_day,track FROM enrollments WHERE user_id=?", (user_id,)).fetchone()
            if not enrollment or enrollment["next_day"] > 30:
                raise DomainError("course_not_active", 409)
            candidates = self.db.execute("SELECT content_id,version,object_json FROM content WHERE kind='day' AND status='PUBLISHED'").fetchall()
            matches = [x for x in candidates if (obj := json.loads(x["object_json"])).get("track") == enrollment["track"] and obj.get("courseDay") == enrollment["next_day"]]
            if len(matches) != 1:
                raise DomainError("published_day_missing_or_ambiguous", 409)
            item = matches[0]
            self.db.execute("INSERT INTO completions VALUES(?,?,?,?,?,?)", (user_id, enrollment["next_day"], item["content_id"], item["version"], evidence_id, now()))
            self.db.execute("UPDATE enrollments SET next_day=next_day+1,status=CASE WHEN next_day=30 THEN 'COMPLETED' ELSE status END WHERE user_id=?", (user_id,))
            self.audit(user_id, "COMPLETE_DAY", item["content_id"])
            self.db.execute("COMMIT")
            return {"completedDay": enrollment["next_day"], "idempotent": False}
        except Exception:
            self.db.execute("ROLLBACK")
            raise
