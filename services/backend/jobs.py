import json
import logging
import threading
import time

from . import pipeline
from .core import PROJECTS, RUNTIME, db, gpu_status, now, query, safe, uid

LOG = logging.getLogger("soceth.jobs")


class Cancelled(Exception):
    pass


class Context:
    def __init__(self, job, stopped):
        self.id = job["id"]
        self.project = job["project"]
        self.stopped = stopped
        base = safe(PROJECTS, self.project) if self.project else RUNTIME
        self.out = safe(base, "runs", self.id)
        self.out.mkdir(parents=True, exist_ok=True)
        self.latest = None

    def check(self, conn=None):
        if self.stopped.is_set():
            raise InterruptedError("Soceth stopped while this job was running. Retry to resume.")
        sql = "SELECT cancel FROM jobs WHERE id=?"
        row = conn.execute(sql, (self.id,)).fetchone() if conn else query(sql, (self.id,))
        if row["cancel"]:
            raise Cancelled("Cancelled by user.")

    def stage(self, stage, progress=None, detail=None):
        label = f"{stage} · {detail}" if detail else stage
        state = (label, round(progress, 4) if progress is not None else None)
        if self.latest != state:
            with db() as conn:
                conn.execute("UPDATE jobs SET stage=?,progress=?,updated=? WHERE id=?",
                             (*state, now(), self.id))
            self.latest = state
            LOG.info("stage", extra={"job": self.id, "stage": label, "progress": state[1]})

    def provider(self, identifier):
        with db() as conn:
            conn.execute("UPDATE jobs SET provider_id=?,updated=? WHERE id=?", (identifier, now(), self.id))


def enqueue(project, kind, body):
    identifier = uid()
    with db() as conn:
        conn.execute("""INSERT INTO jobs(id,project,kind,status,stage,body,created,updated)
            VALUES (?,?,?,'queued','Queued',?,?,?)""",
            (identifier, project, kind, json.dumps(body), now(), now()))
    return {"id": identifier}


def next_job():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if job:
            conn.execute("UPDATE jobs SET status='running',stage='Preparing',updated=? WHERE id=?", (now(), job["id"]))
    return query("SELECT * FROM jobs WHERE id=?", (job["id"],)) if job else None


def track_gpu(stop, peaks):
    while not stop.is_set():
        for index, gpu in enumerate(gpu_status()):
            key = f"{index}: {gpu['name']}"
            peaks[key] = max(peaks.get(key, 0), gpu["used"])
        stop.wait(2)


def run_job(job, stop):
    status, error = "completed", None
    monitor, peaks, begin = threading.Event(), {}, time.monotonic()
    sampler = None
    try:
        ctx = Context(job, stop)
        ctx.check()
        if job["kind"] in {"concept", "generate", "interpret"}:
            sampler = threading.Thread(target=track_gpu, args=(monitor, peaks), daemon=True)
            sampler.start()
        pipeline.run(ctx, job)
    except Cancelled as exc:
        status, error = "cancelled", str(exc)
    except Exception as exc:
        status = "failed"
        error = str(exc) or exc.__class__.__name__
        if any(fragment in error.lower() for fragment in ("out of memory", "outofmemory", "cuda oom", "cublas_status_alloc_failed")):
            error += " Use LOW_VRAM, close other GPU-heavy apps, or retry on a GPU with more memory."
        LOG.exception("job_failed", extra={"job": job["id"]})
    finally:
        monitor.set()
        if sampler:
            sampler.join(timeout=5)
        elapsed = round(time.monotonic() - begin, 2)
        if status != "completed":
            with db() as conn:
                conn.execute("UPDATE jobs SET status=?,stage=?,error=?,updated=? WHERE id=? AND status='running'",
                             (status, status.title(), error[:4000], now(), job["id"]))
        if sampler:
            with db() as conn:
                row = conn.execute("SELECT result FROM jobs WHERE id=?", (job["id"],)).fetchone()
                result = json.loads(row["result"]) if row["result"] else {}
                result["metrics"] = {"gpu_peak_system_mib": dict(peaks), "seconds": elapsed}
                conn.execute("UPDATE jobs SET result=?,updated=? WHERE id=?", (json.dumps(result), now(), job["id"]))
        LOG.info("job_finished", extra={"job": job["id"], "status": status, "seconds": elapsed, "gpu_peak_system_mib": peaks})


def worker(stop):
    while not stop.is_set():
        try:
            job = next_job()
            if job:
                run_job(job, stop)
            else:
                stop.wait(0.4)
        except Exception:
            LOG.exception("worker_error")
            stop.wait(1)

