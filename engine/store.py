"""Postgres store: threats, cases, shared identifiers (campaign links) and an audit log.

Every state change goes through here and is written to audit_log.
"""
import json
import uuid
from datetime import datetime, timezone

from engine.db import Database, migrate
from engine.settings import get_settings


# Identifiers strong enough to link two threats into one campaign. A shared
# registrar, host or copied favicon is NOT enough: it would smear innocents.
LINKING_KINDS = ("phone", "wallet", "telegram")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Store:
    def __init__(self, url: str | None = None, migrate_schema: bool = True):
        url = url or get_settings().database_url
        if migrate_schema:
            migrate(url)
        self.db = Database(url)

    def audit(self, actor: str, action: str, ref: str | None = None, detail: object = None) -> None:
        self.db.execute("INSERT INTO audit_log VALUES (?,?,?,?,?)",
                        (now(), actor, action, ref, json.dumps(detail) if detail is not None else None))
        self.db.commit()

    def add_threat(self, brand_id: str, hostname: str, url: str | None, source: str,
                   source_ref: str | None, stage_a: dict | None) -> str | None:
        """Insert a new threat. Returns its id, or None if we already track this hostname."""
        tid = new_id("thr")
        cur = self.db.execute(
            "INSERT INTO threats (id, brand_id, hostname, url, source, source_ref, detected_at,"
            " stage_a, status, updated_at) VALUES (?,?,?,?,?,?,?,?, 'queued', ?) ON CONFLICT DO NOTHING",
            (tid, brand_id, hostname, url, source, source_ref, now(), json.dumps(stage_a), now()))
        self.db.commit()
        if cur.rowcount == 0:
            return None
        self.audit("system", "threat_detected", tid, {"hostname": hostname, "source": source})
        return tid

    def save_result(self, tid: str, capture: dict, verdict: dict) -> None:
        status = "scored" if verdict["confidence"] != "unknown" else "error"
        self.db.execute(
            "UPDATE threats SET capture=?, verdict=?, confidence=?, score=?, status=?, updated_at=? WHERE id=?",
            (json.dumps(capture), json.dumps(verdict), verdict["confidence"], verdict["score"], status, now(), tid))
        self.db.commit()
        self.audit("system", "threat_scored", tid,
                   {"confidence": verdict["confidence"], "reason_codes": verdict["reason_codes"]})

    def link_identifiers(self, tid: str, foreign: dict, telegram: list[str]) -> None:
        rows = [(tid, "phone", p) for p in foreign.get("phones", [])]
        rows += [(tid, "wallet", w) for w in foreign.get("wallets", [])]
        rows += [(tid, "telegram", t.lower()) for t in telegram]
        self.db.executemany("INSERT INTO identifiers VALUES (?,?,?) ON CONFLICT DO NOTHING", rows)
        self.db.commit()

    def assign_case(self, tid: str, brand_id: str, title: str) -> tuple[str, list[dict]]:
        """Join the open case of any threat sharing a linking identifier, else open a new one.

        Returns (case_id, links) where links lists the shared identifiers that justified a join.
        """
        links = [dict(r) for r in self.db.execute(
            f"""SELECT DISTINCT t.case_id, i2.kind, i2.value, t.hostname
                FROM identifiers i1
                JOIN identifiers i2 ON i1.kind = i2.kind AND i1.value = i2.value AND i2.threat_id != i1.threat_id
                JOIN threats t ON t.id = i2.threat_id
                JOIN cases c ON c.id = t.case_id AND c.status = 'open'
                WHERE i1.threat_id = ? AND t.brand_id = ? AND i1.kind IN ({",".join("?" * len(LINKING_KINDS))})""",
            (tid, brand_id, *LINKING_KINDS))]
        if links:
            case_id = links[0]["case_id"]
            self.audit("system", "threat_linked_to_case", tid, {"case_id": case_id, "shared": links})
        else:
            case_id = new_id("case")
            self.db.execute("INSERT INTO cases VALUES (?,?,?, 'open', ?)", (case_id, brand_id, now(), title))
            self.audit("system", "case_opened", case_id, {"first_threat": tid})
        self.db.execute("UPDATE threats SET case_id=?, updated_at=? WHERE id=?", (case_id, now(), tid))
        self.db.commit()
        return case_id, links

    def save_enrichment(self, tid: str, enrichment: dict) -> None:
        self.db.execute("UPDATE threats SET enrichment=?, updated_at=? WHERE id=?",
                        (json.dumps(enrichment), now(), tid))
        self.db.commit()

    # ---- reads -------------------------------------------------------------

    JSON_COLS = ("stage_a", "capture", "verdict", "enrichment", "evidence_refs", "detail")

    def _row(self, r) -> dict:
        d = dict(r)
        for k in self.JSON_COLS:
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d

    def threats(self, brand_id: str, limit: int = 500, confidence: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM threats WHERE brand_id=?", [brand_id]
        if confidence:
            q, args = q + " AND confidence=?", args + [confidence]
        q += " ORDER BY detected_at DESC LIMIT ?"
        return [self._row(r) for r in self.db.execute(q, (*args, limit))]

    def threat(self, tid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM threats WHERE id=?", (tid,)).fetchone()
        return self._row(r) if r else None

    def cases(self, brand_id: str) -> list[dict]:
        rows = self.db.execute(
            """SELECT c.*, COUNT(DISTINCT t.id) AS threat_count, MAX(t.score) AS max_score,
                      MIN(t.detected_at) AS first_detected,
                      (SELECT COUNT(*) FROM notices n WHERE n.case_id = c.id) AS notice_count,
                      (SELECT COUNT(*) FROM notices n WHERE n.case_id = c.id AND n.status='filed_dry_run') AS filed_count,
                      (SELECT COUNT(*) FROM clock_events e WHERE e.case_id = c.id AND e.event='removed') AS removed_count
               FROM cases c LEFT JOIN threats t ON t.case_id = c.id
               WHERE c.brand_id=? GROUP BY c.id ORDER BY c.opened_at DESC""", (brand_id,))
        return [dict(r) for r in rows]

    def case(self, case_id: str) -> dict | None:
        c = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not c:
            return None
        threats = [self._row(r) for r in self.db.execute(
            "SELECT * FROM threats WHERE case_id=? ORDER BY detected_at", (case_id,))]
        ids = [self._row(r) for r in self.db.execute(
            """SELECT i.kind, i.value, string_agg(t.hostname, ' | ') AS hosts, COUNT(*) AS n
               FROM identifiers i JOIN threats t ON t.id = i.threat_id
               WHERE t.case_id=? GROUP BY i.kind, i.value ORDER BY n DESC""", (case_id,))]
        return {**dict(c), "threats": threats, "identifiers": ids,
                "notices": self.notices(case_id), "clock": self.clock(case_id),
                "audit": [self._row(r) for r in self.db.execute(
                    """SELECT * FROM audit_log WHERE ref=? OR ref IN (SELECT id FROM threats WHERE case_id=?)
                       OR ref IN (SELECT id FROM notices WHERE case_id=?) ORDER BY ts""",
                    (case_id, case_id, case_id))]}

    # ---- notices and clock -------------------------------------------------

    def add_notices(self, case_id: str, drafts: list[dict], threat_id: str) -> int:
        n = 0
        for d in drafts:
            nid = new_id("ntc")
            cur = self.db.execute(
                """INSERT INTO notices (id, case_id, threat_id, channel, channel_name, method, recipient,
                   recipient_verified, subject, body, evidence_refs, status, drafted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, 'drafted', ?) ON CONFLICT DO NOTHING""",
                (nid, case_id, threat_id, d["channel"], d["channel_name"], d["method"], d["recipient"],
                 int(d["recipient_verified"]), d["subject"], d["body"], json.dumps(d["evidence_refs"]), now()))
            if cur.rowcount:
                n += 1
                self.audit("system", "notice_drafted", nid, {"channel": d["channel"], "threat_id": threat_id})
        self.db.commit()
        return n

    def notices(self, case_id: str) -> list[dict]:
        return [self._row(r) for r in self.db.execute(
            "SELECT * FROM notices WHERE case_id=? ORDER BY drafted_at, channel", (case_id,))]

    def all_notices(self, brand_id: str, status: str | None = None) -> list[dict]:
        """Notices across cases, with the case title and host, for the approvals inbox."""
        q = """SELECT n.*, c.title AS case_title, t.hostname AS host
               FROM notices n JOIN cases c ON c.id = n.case_id LEFT JOIN threats t ON t.id = n.threat_id
               WHERE c.brand_id=?"""
        args: list = [brand_id]
        if status:
            q += " AND n.status=?"
            args.append(status)
        q += " ORDER BY n.drafted_at DESC, n.channel"
        return [self._row(r) for r in self.db.execute(q, args)]

    def notice(self, nid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM notices WHERE id=?", (nid,)).fetchone()
        return self._row(r) if r else None

    def approve_notice(self, nid: str, approver: str) -> None:
        self.db.execute("UPDATE notices SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                        (approver, now(), nid))
        self.db.commit()
        self.audit(approver, "notice_approved", nid)

    def reject_notice(self, nid: str, approver: str, reason: str) -> None:
        self.db.execute("UPDATE notices SET status='rejected', approved_by=?, approved_at=? WHERE id=?",
                        (approver, now(), nid))
        self.db.commit()
        self.audit(approver, "notice_rejected", nid, {"reason": reason})

    def file_notice(self, nid: str, actor: str) -> None:
        n = self.notice(nid)
        ts = now()
        self.db.execute("UPDATE notices SET status='filed_dry_run', filed_at=? WHERE id=?", (ts, nid))
        self.clock_event(n["case_id"], nid, n["channel"], "filed", "Dry run: nothing was sent.", ts=ts)
        self.audit(actor, "notice_filed_dry_run", nid)

    def clock_event(self, case_id: str, notice_id: str | None, channel: str | None, event: str,
                    note: str | None = None, ts: str | None = None, simulated: bool = True) -> None:
        self.db.execute(
            "INSERT INTO clock_events (case_id, notice_id, channel, event, ts, simulated, note) VALUES (?,?,?,?,?,?,?)",
            (case_id, notice_id, channel, event, ts or now(), int(simulated), note))
        self.db.commit()
        if event != "filed":
            self.audit("operator", f"clock_{event}", notice_id or case_id, {"note": note, "simulated": simulated})

    def clock(self, case_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM clock_events WHERE case_id=? ORDER BY ts, id", (case_id,))]

    def all_clock(self, brand_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            """SELECT e.*, (SELECT MIN(t.detected_at) FROM threats t WHERE t.case_id = e.case_id) AS detected_at
               FROM clock_events e JOIN cases c ON c.id = e.case_id WHERE c.brand_id=? ORDER BY e.ts""",
            (brand_id,))]

    def counts(self, brand_id: str) -> dict:
        one = lambda q: self.db.execute(q, (brand_id,)).fetchone()[0]  # noqa: E731
        by_conf = {r[0]: r[1] for r in self.db.execute(
            "SELECT COALESCE(confidence,'pending'), COUNT(*) FROM threats WHERE brand_id=? GROUP BY 1", (brand_id,))}
        return {
            "threats": one("SELECT COUNT(*) FROM threats WHERE brand_id=?"),
            "by_confidence": by_conf,
            "open_cases": one("SELECT COUNT(*) FROM cases WHERE brand_id=? AND status='open'"),
            "notices_drafted": one("SELECT COUNT(*) FROM notices n JOIN cases c ON c.id=n.case_id WHERE c.brand_id=?"),
            "notices_awaiting_approval": one(
                "SELECT COUNT(*) FROM notices n JOIN cases c ON c.id=n.case_id WHERE c.brand_id=? AND n.status='drafted'"),
            "notices_filed": one(
                "SELECT COUNT(*) FROM notices n JOIN cases c ON c.id=n.case_id WHERE c.brand_id=? AND n.status='filed_dry_run'"),
        }
