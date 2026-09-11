"""Transactional application state, fenced work leases and version-bound approvals."""
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import (JSON, Column, Float, Integer, MetaData, String, Table, UniqueConstraint,
                        create_engine, insert, select, update)

active_job = ContextVar('active_job', default=None)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.engine = create_engine(settings.database_url, pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {})
        self.meta = MetaData()
        def common():
            return [Column("id", String(64), primary_key=True), Column("tenant", String(120), index=True),
                    Column("data", JSON, nullable=False), Column("created", Float, nullable=False)]
        self.matters = Table("matters", self.meta, *common(), Column("revision", Integer, nullable=False))
        self.documents = Table("documents", self.meta, *common(), Column("matter_id", String(64), index=True),
                               Column("ordinal", Integer, nullable=False))
        self.reviews = Table("reviews", self.meta, *common(), Column("matter_id", String(64), index=True),
            Column("revision", Integer), Column("status", String(40), index=True), Column("request_key", String(150)),
            UniqueConstraint("tenant", "matter_id", "request_key"))
        self.jobs = Table("jobs", self.meta, Column("id", String(64), primary_key=True), Column("tenant", String(120)),
            Column("status", String(30)), Column("payload", JSON), Column("lease", String(64)),
            Column("lease_until", Float), Column("attempts", Integer), Column("updated", Float))
        self.tasks = Table("tasks", self.meta, *common(), Column("matter_id", String(64), index=True),
                          Column("status", String(30)), Column("revision", Integer))
        self.audit = Table("audit", self.meta, *common(), Column("matter_id", String(64), index=True))
        self.heartbeats = Table("heartbeats", self.meta, Column("id", String(100), primary_key=True), Column("seen", Float))
        self.limits = Table("rate_limits", self.meta, Column("id", String(200), primary_key=True),
                            Column("count", Integer), Column("expires", Float))
        self.meta.create_all(self.engine)

    @contextmanager
    def tx(self):
        with self.engine.connect() as conn:
            if self.engine.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _one(self, conn, table, tenant, ident, lock=False):
        stmt = select(table).where(table.c.tenant == tenant, table.c.id == ident)
        if lock:
            stmt = stmt.with_for_update()
        row = conn.execute(stmt).mappings().first()
        if not row:
            raise NotFound(ident)
        return dict(row)

    @staticmethod
    def public(row):
        return {**row["data"], **{k: v for k, v in row.items() if k != "data"}}

    def get(self, table, tenant, ident):
        with self.engine.connect() as conn:
            return self.public(self._one(conn, table, tenant, ident))

    def list(self, table, tenant, matter_id=None, limit=100):
        stmt = select(table).where(table.c.tenant == tenant)
        if matter_id is not None:
            stmt = stmt.where(table.c.matter_id == matter_id)
        with self.engine.connect() as conn:
            return [self.public(dict(x)) for x in conn.execute(stmt.order_by(table.c.created.desc()).limit(limit)).mappings()]

    def _audit(self, conn, tenant, matter_id, event, **data):
        conn.execute(insert(self.audit).values(id=str(uuid.uuid4()), tenant=tenant, matter_id=matter_id,
            created=time.time(), data={"event": event, **data}))

    def event(self, tenant, matter_id, event, **data):
        with self.tx() as conn:
            self._audit(conn, tenant, matter_id, event, **data)

    def create_matter(self, tenant, title, owner, playbook):
        ident = str(uuid.uuid4())
        with self.tx() as conn:
            conn.execute(insert(self.matters).values(id=ident, tenant=tenant, revision=0, created=time.time(),
                data={"title": title, "owner": owner, "playbook": playbook}))
            self._audit(conn, tenant, ident, "matter_created")
        return self.get(self.matters, tenant, ident)

    def _invalidate(self, conn, tenant, matter_id):
        conn.execute(update(self.reviews).where(self.reviews.c.tenant == tenant,
            self.reviews.c.matter_id == matter_id).values(status="stale"))
        conn.execute(update(self.tasks).where(self.tasks.c.tenant == tenant,
            self.tasks.c.matter_id == matter_id).values(status="stale"))

    def add_document(self, tenant, matter_id, document, clauses, source):
        ident = str(uuid.uuid4())
        with self.tx() as conn:
            matter = self._one(conn, self.matters, tenant, matter_id, True)
            rows = conn.execute(select(self.documents.c.id).where(self.documents.c.tenant == tenant,
                                self.documents.c.matter_id == matter_id)).all()
            if len(rows) >= self.settings.max_documents:
                raise Conflict("Document capacity reached")
            if document.get("supersedes"):
                parent = self._one(conn, self.documents, tenant, document["supersedes"])
                if parent["matter_id"] != matter_id:
                    raise Conflict("Superseded document must belong to this matter")
            revision = matter["revision"] + 1
            conn.execute(insert(self.documents).values(id=ident, tenant=tenant, matter_id=matter_id,
                ordinal=revision, created=time.time(), data={**document, "clauses": clauses, "source": source,
                    "content_hash": hashlib.sha256(document["text"].encode()).hexdigest()}))
            conn.execute(update(self.matters).where(self.matters.c.id == matter_id).values(revision=revision))
            self._invalidate(conn, tenant, matter_id)
            self._audit(conn, tenant, matter_id, "document_added", document_id=ident, revision=revision)
        return self.get(self.documents, tenant, ident)

    def update_playbook(self, tenant, matter_id, playbook):
        with self.tx() as conn:
            matter = self._one(conn, self.matters, tenant, matter_id, True)
            conn.execute(update(self.matters).where(self.matters.c.id == matter_id).values(
                revision=matter["revision"] + 1, data={**matter["data"], "playbook": playbook}))
            self._invalidate(conn, tenant, matter_id)
            self._audit(conn, tenant, matter_id, "playbook_changed")
        return self.get(self.matters, tenant, matter_id)

    def create_review(self, tenant, matter_id, request_key, subject):
        with self.tx() as conn:
            matter = self._one(conn, self.matters, tenant, matter_id, True)
            existing = conn.execute(select(self.reviews).where(self.reviews.c.tenant == tenant,
                self.reviews.c.matter_id == matter_id, self.reviews.c.request_key == request_key)).mappings().first()
            if existing:
                if existing["revision"] != matter["revision"]:
                    raise Conflict("Idempotency key belongs to an older revision")
                return self.public(dict(existing))
            pending = conn.execute(select(self.jobs.c.id).where(self.jobs.c.tenant == tenant,
                self.jobs.c.status.in_(["queued", "running"]))).all()
            if len(pending) >= self.settings.max_jobs:
                raise Conflict("Review queue capacity reached")
            docs = conn.execute(select(self.documents.c.id).where(self.documents.c.tenant == tenant,
                self.documents.c.matter_id == matter_id).order_by(self.documents.c.ordinal)).scalars().all()
            if not docs:
                raise Conflict("Add an agreement first")
            ident = str(uuid.uuid4())
            conn.execute(insert(self.reviews).values(id=ident, tenant=tenant, matter_id=matter_id,
                revision=matter["revision"], request_key=request_key, status="queued", created=time.time(),
                data={"document_ids": docs, "playbook": matter["data"]["playbook"], "owner": matter["data"]["owner"],
                      "created_by": subject}))
            conn.execute(insert(self.jobs).values(id=ident, tenant=tenant, status="queued", payload={},
                attempts=0, lease=None, lease_until=0, updated=time.time()))
            self._audit(conn, tenant, matter_id, "review_queued", review_id=ident)
        return self.get(self.reviews, tenant, ident)

    def current_review(self, conn, tenant, review_id):
        review = self._one(conn, self.reviews, tenant, review_id)
        matter = self._one(conn, self.matters, tenant, review["matter_id"], True)
        review = self._one(conn, self.reviews, tenant, review_id, True)
        job = active_job.get()
        if job:
            lease = conn.execute(select(self.jobs).where(self.jobs.c.id == review_id).with_for_update()).mappings().one()
            if lease['lease'] != job['lease'] or lease['status'] != 'running' or lease['lease_until'] < time.time():
                raise Conflict('Worker lease expired; write refused')
        if review["revision"] != matter["revision"] or review["status"] == "stale":
            raise Conflict("Source changed; start a review of the current revision")
        return review

    def save_proposal(self, tenant, review_id, proposal):
        with self.tx() as conn:
            review = self.current_review(conn, tenant, review_id)
            conn.execute(update(self.reviews).where(self.reviews.c.id == review_id).values(
                status="awaiting_approval", data={**review["data"], "proposal": proposal}))
            self._audit(conn, tenant, review["matter_id"], "proposal_ready", review_id=review_id,
                        plan_hash=proposal["plan_hash"])

    def decide(self, tenant, review_id, decision, subject):
        with self.tx() as conn:
            review = self.current_review(conn, tenant, review_id)
            if review["status"] != "awaiting_approval":
                raise Conflict("Review is not awaiting a decision")
            job = conn.execute(select(self.jobs).where(self.jobs.c.id == review_id).with_for_update()).mappings().one()
            if job['status'] != 'waiting':
                raise Conflict('Checkpoint is still being saved; retry shortly')
            proposal = review["data"]["proposal"]
            if decision["plan_hash"] != proposal["plan_hash"]:
                raise Conflict("Approval must match the current proposal")
            if decision["decision"] == "approve":
                if proposal.get("blocked"):
                    raise Conflict("Blocked findings cannot be approved; correct the source first")
                required = {f for a in proposal["actions"] for f in a["required_finding_ids"]}
                if not required.issubset(set(decision["confirmed_finding_ids"])):
                    raise Conflict("Confirm each source finding used by the proposed actions")
            approved = {**decision, "subject": subject, "at": time.time()}
            conn.execute(update(self.reviews).where(self.reviews.c.id == review_id).values(status="resume_queued",
                         data={**review["data"], "decision": approved}))
            conn.execute(update(self.jobs).where(self.jobs.c.id == review_id).values(status="queued",
                payload={"resume": approved}, lease=None, lease_until=0, attempts=0, updated=time.time()))
            self._audit(conn, tenant, review["matter_id"], "review_decision", review_id=review_id,
                        decision=decision["decision"], subject=subject)

    def execute_actions(self, tenant, review_id):
        with self.tx() as conn:
            review = self.current_review(conn, tenant, review_id)
            approval = review["data"].get("decision", {})
            proposal = review["data"]["proposal"]
            if approval.get("decision") != "approve" or approval.get("plan_hash") != proposal["plan_hash"]:
                raise Conflict("No matching approval")
            task_ids = []
            for action in proposal["actions"]:
                ident = digest([tenant, review["matter_id"], action["kind"]])[:40]
                data = {**action, "review_id": review_id, "plan_hash": proposal["plan_hash"],
                        "owner": review["data"]["owner"], "approved_by": approval["subject"]}
                existing = conn.execute(select(self.tasks).where(self.tasks.c.id == ident)).mappings().first()
                if existing:
                    previous = existing["data"]
                    if previous.get("plan_hash") == proposal["plan_hash"]:
                        task_ids.append(ident)
                        continue
                    if previous.get("external_url"):
                        data["external_url"] = previous["external_url"]
                        data["external_sync_status"] = "update_required"
                    conn.execute(update(self.tasks).where(self.tasks.c.id == ident).values(data=data,
                        revision=review["revision"], status="open"))
                else:
                    conn.execute(insert(self.tasks).values(id=ident, tenant=tenant, matter_id=review["matter_id"],
                        created=time.time(), status="open", revision=review["revision"], data=data))
                task_ids.append(ident)
            self._audit(conn, tenant, review["matter_id"], "approved_actions_applied", review_id=review_id,
                        task_ids=task_ids)
        return task_ids

    def finish_review(self, tenant, review_id, status, **data):
        with self.tx() as conn:
            review = self.current_review(conn, tenant, review_id)
            conn.execute(update(self.reviews).where(self.reviews.c.id == review_id).values(
                status=status, data={**review["data"], **data}))
            self._audit(conn, tenant, review["matter_id"], "review_finished", review_id=review_id, status=status)

    def claim(self, worker):
        now = time.time()
        with self.tx() as conn:
            stmt = select(self.jobs).where((self.jobs.c.status == "queued") |
                ((self.jobs.c.status == "running") & (self.jobs.c.lease_until < now))).order_by(self.jobs.c.updated)
            row = conn.execute(stmt.with_for_update(skip_locked=True).limit(1)).mappings().first()
            if not row:
                return None
            row = dict(row)
            if row["attempts"] >= 3:
                conn.execute(update(self.jobs).where(self.jobs.c.id == row["id"]).values(status="failed"))
                conn.execute(update(self.reviews).where(self.reviews.c.id == row['id'],
                    self.reviews.c.status != 'stale').values(status='failed'))
                return None
            token = str(uuid.uuid4())
            conn.execute(update(self.jobs).where(self.jobs.c.id == row["id"]).values(status="running", lease=token,
                lease_until=now + self.settings.lease_seconds, attempts=row["attempts"] + 1, updated=now))
            return {**row, "lease": token, "worker": worker}

    def renew(self, job):
        with self.tx() as conn:
            return conn.execute(update(self.jobs).where(self.jobs.c.id == job["id"],
                self.jobs.c.lease == job["lease"], self.jobs.c.status == "running").values(
                lease_until=time.time() + self.settings.lease_seconds, updated=time.time())).rowcount == 1

    def finish_job(self, job, status):
        with self.tx() as conn:
            conn.execute(update(self.jobs).where(self.jobs.c.id == job["id"], self.jobs.c.lease == job["lease"]).values(
                status=status, updated=time.time(), lease_until=0))

    def heartbeat(self, worker):
        with self.tx() as conn:
            if conn.execute(select(self.heartbeats.c.id).where(self.heartbeats.c.id == worker)).first():
                conn.execute(update(self.heartbeats).where(self.heartbeats.c.id == worker).values(seen=time.time()))
            else:
                conn.execute(insert(self.heartbeats).values(id=worker, seen=time.time()))

    def operations(self, tenant):
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.jobs.c.status).where(self.jobs.c.tenant == tenant)).scalars().all()
            seen = conn.execute(select(self.heartbeats.c.seen).order_by(self.heartbeats.c.seen.desc()).limit(1)).scalar()
        return {"jobs": {s: rows.count(s) for s in set(rows)},
                "worker_age_seconds": None if seen is None else round(time.time() - seen, 2)}
