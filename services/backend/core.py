import contextlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .locations import DATA, LOGS, PROJECTS, ROOT, RUNTIME

PORT = int(os.getenv("SOCETH_PORT", "17863"))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN_FILE = DATA / "local.token"
SCHEMA = 1


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def safe(base, *parts):
    path = base.joinpath(*parts).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("Path escapes the configured storage directory.")
    return path


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uid()}.tmp")
    try:
        with temp.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    atomic(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode())


MIGRATIONS = [
    """
    CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE bibles(project TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
       body TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project, version));
    CREATE TABLE assets(id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id),
       name TEXT NOT NULL, category TEXT NOT NULL, body TEXT NOT NULL, current TEXT, updated TEXT NOT NULL);
    CREATE TABLE revisions(id TEXT PRIMARY KEY, asset TEXT NOT NULL REFERENCES assets(id),
       parent TEXT REFERENCES revisions(id), body TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE files(id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id),
       kind TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE jobs(id TEXT PRIMARY KEY, project TEXT REFERENCES projects(id), kind TEXT NOT NULL,
       status TEXT NOT NULL, stage TEXT NOT NULL, progress REAL, body TEXT NOT NULL,
       result TEXT, error TEXT, cancel INTEGER NOT NULL DEFAULT 0, provider_id TEXT,
       created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE exports(id TEXT PRIMARY KEY, asset TEXT NOT NULL REFERENCES assets(id),
       revision TEXT NOT NULL REFERENCES revisions(id), file TEXT NOT NULL REFERENCES files(id),
       body TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE installations(id TEXT PRIMARY KEY, body TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX jobs_queue ON jobs(status, created);
    CREATE INDEX assets_project ON assets(project, updated);
    CREATE INDEX revisions_asset ON revisions(asset, created);
    """
]


@contextlib.contextmanager
def db():
    connection = sqlite3.connect(DATA / "studio.sqlite3", timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init():
    for location in (DATA, PROJECTS, LOGS, RUNTIME):
        location.mkdir(parents=True, exist_ok=True)
    try:
        with TOKEN_FILE.open("x", encoding="ascii") as target:
            target.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError("This database was created with a newer Soceth release.")
        for number, migration in enumerate(MIGRATIONS[version:], version + 1):
            conn.executescript(f"BEGIN IMMEDIATE;{migration}PRAGMA user_version={number};COMMIT;")
        conn.execute("""UPDATE jobs SET status='failed',stage='Interrupted',error=?,updated=?
            WHERE status='running'""", ("Previous session stopped before commit. Retry to restart or resume.", now()))
    return TOKEN_FILE.read_text(encoding="ascii").strip()


def unpack(row):
    if row is None:
        return None
    value = dict(row)
    for key in ("body", "result", "value"):
        if key in value and value[key] is not None:
            value[key] = json.loads(value[key])
    return value


def query(sql, params=(), many=False):
    with db() as conn:
        rows = conn.execute(sql, params)
        return [unpack(row) for row in rows] if many else unpack(rows.fetchone())


def setting(key, default=None):
    row = query("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))


def file_record(conn, project, path, kind, name, body=None):
    item = uid()
    relative = path.resolve().relative_to(PROJECTS.resolve()).as_posix()
    conn.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?)",
                 (item, project, kind, relative, name, json.dumps(body or {}), now()))
    return item


def project_file(project, file_id, kinds):
    file = query("SELECT * FROM files WHERE id=? AND project=?", (file_id, project))
    if not file or file["kind"] not in kinds:
        raise ValueError("The selected file does not belong to this project or has the wrong type.")
    return safe(PROJECTS, file["path"]), file


def blender_path():
    configured = os.getenv("SOCETH_BLENDER")
    if configured:
        return Path(configured) if Path(configured).is_file() else None
    discovered = shutil.which("blender")
    if discovered:
        return Path(discovered)
    foundation = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Blender Foundation"
    def version(path):
        return tuple(int(number) for number in re.findall(r"\d+", path.parent.name))
    return next(iter(sorted(foundation.glob("Blender */blender.exe"), key=version, reverse=True)), None)


def gpu_status():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4,
            check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [{"name": name, "total": int(total), "used": int(used), "driver": driver}
                for line in result.stdout.splitlines()
                for name, total, used, driver in [[part.strip() for part in line.split(",")]]]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def profile(gpus=None):
    manual = setting("profile", "AUTO")
    if manual != "AUTO":
        return "HIGH_QUALITY" if manual == "QUALITY" else "BALANCED" if manual == "CUSTOM" else manual
    cards = gpu_status() if gpus is None else gpus
    memory = max((item["total"] for item in cards), default=0)
    return "HIGH_QUALITY" if memory >= 16384 else "BALANCED" if memory >= 12288 else "LOW_VRAM"

