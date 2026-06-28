"""Pelanggan Bulanan ISM30 — FastAPI app."""
from __future__ import annotations

import logging
import random
import string
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from mikrotik import MikrotikClient
from storage import Storage
from whatsapp import WuzAPIClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
cfg = yaml.safe_load(Path("configs/ism30.yaml").read_text())

ADMIN_WA      = cfg["admin_wa"]
HARGA         = cfg["harga_bulanan"]
WUZAPI_URL    = cfg["wuzapi"]["url"]
WUZAPI_TOKEN  = cfg["wuzapi"]["token"]
DB_PATH       = cfg["db_path"]

storage = Storage(DB_PATH)
mt      = MikrotikClient(cfg["mikrotik_servers"])
wa      = WuzAPIClient(WUZAPI_URL, WUZAPI_TOKEN)

profiles_cache: list[str] = cfg.get("profiles", [])


def gen_password(length=8) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def bulan_ini() -> str:
    return date.today().strftime("%Y-%m")


def format_rupiah(n: int) -> str:
    return f"Rp {n:,.0f}".replace(",", ".")


# ── Scheduler ────────────────────────────────────────────────────────────────
def cek_jatuh_tempo():
    hari = date.today().day
    bulan = bulan_ini()
    pelanggan_list = storage.get_jatuh_tempo_hari_ini(hari)
    for p in pelanggan_list:
        tagihan = storage.get_or_create_tagihan(p["id"], bulan)
        if tagihan["lunas"]:
            continue
        # Notif ke pelanggan
        msg_pelanggan = (
            f"Halo {p['nama']},\n\n"
            f"Tagihan internet Anda bulan {date.today().strftime('%B %Y')} "
            f"sebesar *{format_rupiah(HARGA)}* jatuh tempo hari ini.\n\n"
            f"Mohon segera lakukan pembayaran.\n"
            f"Terima kasih 🙏"
        )
        wa.send_message(p["nomor_wa"], msg_pelanggan)
        # Notif ke admin
        msg_admin = (
            f"⏰ *Jatuh Tempo Hari Ini*\n\n"
            f"Nama: {p['nama']}\n"
            f"No WA: {p['nomor_wa']}\n"
            f"Username: {p['username']}\n"
            f"Tagihan: {format_rupiah(HARGA)}\n"
            f"Status: Belum Bayar"
        )
        wa.send_message(ADMIN_WA, msg_admin)
        log.info("Reminder jatuh tempo terkirim: %s", p["nama"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    global profiles_cache
    if not profiles_cache:
        try:
            profiles_cache = await mt.get_profiles()
            log.info("Profiles loaded from Mikrotik: %s", profiles_cache)
        except Exception as e:
            log.warning("Gagal load profiles dari Mikrotik: %s", e)
    else:
        log.info("Profiles from config: %s", profiles_cache)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(cek_jatuh_tempo, "cron", hour=8, minute=0)
    scheduler.start()
    log.info("Scheduler started — cek jatuh tempo setiap hari jam 08:00")
    yield
    scheduler.shutdown()


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
templates.env.globals["format_rupiah"] = format_rupiah
templates.env.globals["bulan_ini"] = bulan_ini


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    bulan = bulan_ini()
    stats = storage.count_stats(bulan)
    hari = date.today().day
    jatuh_tempo = storage.get_jatuh_tempo_hari_ini(hari)
    # Tandai mana yang sudah lunas
    for p in jatuh_tempo:
        t = storage.get_or_create_tagihan(p["id"], bulan)
        p["lunas"] = t["lunas"]
    return templates.TemplateResponse(request=request, name="index.html", context={
        "stats": stats, "jatuh_tempo": jatuh_tempo,
        "bulan": bulan, "hari_ini": date.today().strftime("%d %B %Y"),
    })


@app.get("/tambah", response_class=HTMLResponse)
async def tambah_form(request: Request):
    return templates.TemplateResponse(request=request, name="tambah.html", context={
        "profiles": profiles_cache,
        "today": date.today().isoformat(),
    })


@app.post("/tambah", response_class=HTMLResponse)
async def tambah_submit(
    request: Request,
    nama: str = Form(...),
    alamat: str = Form(""),
    nomor_wa: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    profile: str = Form(...),
    tanggal_mulai: str = Form(...),
    tanggal_bayar: int = Form(...),
    catatan: str = Form(""),
):
    username = username.strip() or nama.lower().replace(" ", "")[:12]
    password = password.strip() or gen_password()

    # Buat user di Mikrotik
    mt_results = await mt.create_hotspot_user(username, password, profile)
    mt_ok = all(r["ok"] for r in mt_results.values())
    mt_gagal = [f"{k}: {v['msg']}" for k, v in mt_results.items() if not v["ok"]]

    if not mt_ok and all(not r["ok"] for r in mt_results.values()):
        return templates.TemplateResponse(request=request, name="tambah.html", context={
            "profiles": profiles_cache,
            "today": date.today().isoformat(),
            "error": "Gagal buat user di semua server: " + ", ".join(mt_gagal),
        })

    pid = storage.create_pelanggan(
        nama, alamat, nomor_wa, username, password, profile,
        tanggal_mulai, tanggal_bayar, catatan
    )

    # WA ke pelanggan
    msg_pelanggan = (
        f"Halo {nama},\n\n"
        f"Akun internet bulanan Anda telah aktif! 🎉\n\n"
        f"Username: *{username}*\n"
        f"Password: *{password}*\n"
        f"Paket: {profile}\n"
        f"Tagihan: {format_rupiah(HARGA)}/bulan\n"
        f"Jatuh tempo: setiap tgl {tanggal_bayar}\n\n"
        f"Terima kasih 🙏"
    )
    wa.send_message(nomor_wa, msg_pelanggan)

    # WA ke admin
    peringatan = f"\n⚠️ Gagal di: {', '.join(mt_gagal)}" if mt_gagal else ""
    msg_admin = (
        f"✅ *Pelanggan Baru Ditambahkan*\n\n"
        f"Nama: {nama}\n"
        f"Alamat: {alamat or '-'}\n"
        f"No WA: {nomor_wa}\n"
        f"Username: {username}\n"
        f"Profile: {profile}\n"
        f"Jatuh Tempo: tgl {tanggal_bayar} setiap bulan"
        f"{peringatan}"
    )
    wa.send_message(ADMIN_WA, msg_admin)

    return RedirectResponse(f"/pelanggan?added={pid}", status_code=303)


@app.get("/pelanggan", response_class=HTMLResponse)
async def daftar_pelanggan(request: Request, added: str = ""):
    bulan = bulan_ini()
    rows = storage.list_pelanggan()
    for r in rows:
        t = storage.get_or_create_tagihan(r["id"], bulan)
        r["lunas"] = t["lunas"]
        r["tanggal_lunas"] = t.get("tanggal_lunas", "")
    return templates.TemplateResponse(request=request, name="pelanggan.html", context={
        "rows": rows, "bulan": bulan, "added": added,
    })


@app.get("/tagihan", response_class=HTMLResponse)
async def tagihan(request: Request, bulan: str = ""):
    bulan = bulan or bulan_ini()
    rows = storage.get_tagihan_bulan(bulan)
    stats = storage.count_stats(bulan)
    return templates.TemplateResponse(request=request, name="tagihan.html", context={
        "rows": rows, "bulan": bulan, "stats": stats,
    })


@app.post("/bayar/{pid}", response_class=JSONResponse)
async def tandai_bayar(pid: str, bulan: str = Form("")):
    bulan = bulan or bulan_ini()
    p = storage.get_pelanggan(pid)
    if not p:
        return JSONResponse({"ok": False, "msg": "Pelanggan tidak ditemukan."}, status_code=404)
    storage.tandai_lunas(pid, bulan)

    msg_admin = (
        f"💰 *Pembayaran Diterima*\n\n"
        f"Nama: {p['nama']}\n"
        f"Bulan: {bulan}\n"
        f"Jumlah: {format_rupiah(HARGA)}"
    )
    wa.send_message(ADMIN_WA, msg_admin)
    return {"ok": True, "msg": f"Pembayaran {bulan} dicatat."}


@app.post("/nonaktif/{pid}", response_class=JSONResponse)
async def nonaktifkan(pid: str):
    p = storage.get_pelanggan(pid)
    if not p:
        return JSONResponse({"ok": False, "msg": "Tidak ditemukan."}, status_code=404)
    storage.update_status(pid, "nonaktif")
    return {"ok": True}


@app.post("/aktifkan/{pid}", response_class=JSONResponse)
async def aktifkan(pid: str):
    p = storage.get_pelanggan(pid)
    if not p:
        return JSONResponse({"ok": False, "msg": "Tidak ditemukan."}, status_code=404)
    storage.update_status(pid, "aktif")
    return {"ok": True}
