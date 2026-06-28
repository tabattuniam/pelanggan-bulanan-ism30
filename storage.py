"""Storage layer — SQLite."""
from __future__ import annotations
import sqlite3
import time
import uuid
from pathlib import Path


class Storage:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init()

    def _conn(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        con = self._conn()
        con.executescript("""
        CREATE TABLE IF NOT EXISTS pelanggan (
            id          TEXT PRIMARY KEY,
            nama        TEXT NOT NULL,
            alamat      TEXT DEFAULT '',
            nomor_wa    TEXT NOT NULL,
            username    TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,
            profile     TEXT NOT NULL,
            tanggal_mulai TEXT NOT NULL,
            tanggal_bayar INTEGER NOT NULL,
            status      TEXT DEFAULT 'aktif',
            catatan     TEXT DEFAULT '',
            created_at  INTEGER,
            updated_at  INTEGER
        );
        CREATE TABLE IF NOT EXISTS pembayaran (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pelanggan_id    TEXT NOT NULL REFERENCES pelanggan(id),
            bulan           TEXT NOT NULL,
            lunas           INTEGER DEFAULT 0,
            tanggal_lunas   TEXT,
            created_at      INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bayar_unique ON pembayaran(pelanggan_id, bulan);
        """)
        con.commit()
        con.close()

    def create_pelanggan(self, nama, alamat, nomor_wa, username, password, profile,
                          tanggal_mulai, tanggal_bayar, catatan="") -> str:
        vid = uuid.uuid4().hex[:8].upper()
        now = int(time.time())
        con = self._conn()
        con.execute(
            """INSERT INTO pelanggan
               (id,nama,alamat,nomor_wa,username,password,profile,
                tanggal_mulai,tanggal_bayar,catatan,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, nama, alamat, nomor_wa, username, password, profile,
             tanggal_mulai, int(tanggal_bayar), catatan, now, now)
        )
        con.commit()
        con.close()
        return vid

    def list_pelanggan(self, status=None) -> list[dict]:
        con = self._conn()
        if status:
            rows = con.execute("SELECT * FROM pelanggan WHERE status=? ORDER BY nama", (status,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM pelanggan ORDER BY nama").fetchall()
        con.close()
        return [dict(r) for r in rows]

    def get_pelanggan(self, pid: str) -> dict | None:
        con = self._conn()
        row = con.execute("SELECT * FROM pelanggan WHERE id=?", (pid,)).fetchone()
        con.close()
        return dict(row) if row else None

    def update_status(self, pid: str, status: str):
        con = self._conn()
        con.execute("UPDATE pelanggan SET status=?, updated_at=? WHERE id=?",
                    (status, int(time.time()), pid))
        con.commit()
        con.close()

    def get_jatuh_tempo_hari_ini(self, hari: int) -> list[dict]:
        con = self._conn()
        rows = con.execute(
            "SELECT * FROM pelanggan WHERE tanggal_bayar=? AND status='aktif' ORDER BY nama",
            (hari,)
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def get_or_create_tagihan(self, pelanggan_id: str, bulan: str) -> dict:
        con = self._conn()
        row = con.execute(
            "SELECT * FROM pembayaran WHERE pelanggan_id=? AND bulan=?",
            (pelanggan_id, bulan)
        ).fetchone()
        if not row:
            con.execute(
                "INSERT OR IGNORE INTO pembayaran (pelanggan_id, bulan, lunas, created_at) VALUES (?,?,0,?)",
                (pelanggan_id, bulan, int(time.time()))
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM pembayaran WHERE pelanggan_id=? AND bulan=?",
                (pelanggan_id, bulan)
            ).fetchone()
        con.close()
        return dict(row)

    def tandai_lunas(self, pelanggan_id: str, bulan: str):
        from datetime import date
        con = self._conn()
        con.execute(
            """INSERT INTO pembayaran (pelanggan_id, bulan, lunas, tanggal_lunas, created_at)
               VALUES (?,?,1,?,?)
               ON CONFLICT(pelanggan_id, bulan) DO UPDATE SET lunas=1, tanggal_lunas=excluded.tanggal_lunas""",
            (pelanggan_id, bulan, date.today().isoformat(), int(time.time()))
        )
        con.commit()
        con.close()

    def get_tagihan_bulan(self, bulan: str) -> list[dict]:
        con = self._conn()
        rows = con.execute("""
            SELECT p.*, COALESCE(b.lunas, 0) as lunas, b.tanggal_lunas
            FROM pelanggan p
            LEFT JOIN pembayaran b ON b.pelanggan_id=p.id AND b.bulan=?
            WHERE p.status='aktif'
            ORDER BY p.tanggal_bayar, p.nama
        """, (bulan,)).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def count_stats(self, bulan: str) -> dict:
        con = self._conn()
        total = con.execute("SELECT COUNT(*) FROM pelanggan WHERE status='aktif'").fetchone()[0]
        lunas = con.execute(
            "SELECT COUNT(*) FROM pembayaran WHERE bulan=? AND lunas=1", (bulan,)
        ).fetchone()[0]
        con.close()
        return {"total": total, "lunas": lunas, "belum": total - lunas}
