# VERSI INI SUDAH ADA RELAY 1 DAN RELAY 2 NYA BISA TERPISAH
# FIX: relay_timeout tersimpan di SQLite + icon tray lebih keren

import ctypes
from ctypes import wintypes
import ctypes.wintypes
import os
import sys
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk
from pynput import mouse
import threading
import ttkbootstrap as tb
import time
from pynput.mouse import Listener
import cv2
import numpy as np
import pyautogui
from PIL import Image, ImageDraw, ImageFont
from pystray import Icon, MenuItem, Menu
import signal
import atexit
import winreg
import math

# ============================================================
# Resolve DLL path — support .py, onefile, dan onedir PyInstaller
# onefile : file diekstrak ke sys._MEIPASS (folder temp)
# onedir  : semua file ada di folder yang sama dengan .exe (sys.executable)
# ============================================================
if getattr(sys, "frozen", False):
    # onefile → ada _MEIPASS; onedir → tidak ada, pakai folder exe
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    BASE_DIR   = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR   = BUNDLE_DIR

# DLL ada di folder bundle (di samping exe untuk onedir, di _MEIPASS untuk onefile)
dll_path = os.path.join(BUNDLE_DIR, "usb_relay_device.dll")

# ============================================================
# SINGLE INSTANCE — Cegah aplikasi dibuka dobel
# Ditambah retry: saat auto-restart, proses lama mungkin belum benar2
# selesai menutup (handle mutex belum dilepas OS), jadi coba beberapa
# kali dengan delay sebelum dianggap "sudah ada instance lain".
# ============================================================
import time as _time_mutex  # import awal khusus untuk retry mutex (time penuh diimport lagi di bawah)

MUTEX_NAME = "URCTouchRelayMutex_UniqueID_12345"
_mutex = None
_MUTEX_RETRY_MAX = 10
_MUTEX_RETRY_DELAY = 0.5  # detik

for _attempt in range(_MUTEX_RETRY_MAX):
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() != 183:
        break
    ctypes.windll.kernel32.CloseHandle(_mutex)
    _mutex = None
    _time_mutex.sleep(_MUTEX_RETRY_DELAY)
else:
    messagebox.showwarning("Peringatan", "Aplikasi sudah dibuka!\nTidak bisa membuka dua aplikasi sekaligus.")
    raise SystemExit("Instance sudah berjalan.")

# ============================================================
# AUTO STARTUP — Registry helpers
# ============================================================
APP_NAME = "URCTouchRelay"
# Auto startup daftarkan batch wrapper supaya restart otomatis jalan
# Kalau relay-start.bat tidak ada (misal jalan manual), fallback ke exe/py
_exe_dir  = BASE_DIR
_bat_path = os.path.join(_exe_dir, "relay-start.bat")
APP_PATH  = _bat_path if os.path.exists(_bat_path) else os.path.abspath(sys.argv[0])
REG_KEY   = r"Software\Microsoft\Windows\CurrentVersion\Run"

def is_startup_registered():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ)
        try:
            existing, _ = winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return existing == APP_PATH
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False

def register_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, APP_PATH)
        winreg.CloseKey(key)
        print(f"[Startup] Terdaftar di registry: {APP_PATH}")
    except Exception as e:
        print(f"[Startup] Gagal daftar registry: {e}")

def unregister_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, APP_NAME)
            print("[Startup] Dihapus dari registry.")
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[Startup] Gagal hapus registry: {e}")

# ============================================================
# SQLite — Inisialisasi database log + settings
# ============================================================
DB_PATH = os.path.join(BASE_DIR, "relay_log.db")
_db_lock = threading.Lock()

def init_db():
    """Buat tabel log dan settings jika belum ada."""
    with _db_lock:
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            CREATE TABLE IF NOT EXISTS relay_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                message   TEXT    NOT NULL
            )
        """)
        # ---- FIX PERBAIKAN 1: tabel settings untuk simpan relay_timeout ----
        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        con.commit()
        con.close()

def db_insert_log(message: str):
    """Simpan satu baris log ke SQLite (thread-safe)."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        try:
            con = sqlite3.connect(DB_PATH)
            con.execute("INSERT INTO relay_log (timestamp, message) VALUES (?, ?)", (ts, message))
            con.commit()
            con.close()
        except Exception as e:
            print(f"[DB] Gagal simpan log: {e}")

def db_save_setting(key: str, value: str):
    """Simpan atau update satu setting ke SQLite."""
    with _db_lock:
        try:
            con = sqlite3.connect(DB_PATH)
            con.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value)
            )
            con.commit()
            con.close()
            print(f"[Settings] {key} = {value} tersimpan.")
        except Exception as e:
            print(f"[DB] Gagal simpan setting: {e}")

def db_load_setting(key: str, default=None):
    """Baca satu setting dari SQLite. Kembalikan default jika tidak ada."""
    with _db_lock:
        try:
            con = sqlite3.connect(DB_PATH)
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            con.close()
            return row[0] if row else default
        except Exception as e:
            print(f"[DB] Gagal baca setting: {e}")
            return default

init_db()

# ---- Load relay_timeout dari DB saat startup ----
relay_timeout = int(db_load_setting("relay_timeout", "300"))
print(f"[Settings] relay_timeout dimuat: {relay_timeout} detik")

# ============================================================
# Definisi konstanta Windows
# ============================================================
WH_MOUSE_LL    = 14
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WH_GETMESSAGE  = 3
WM_TOUCH       = 0x0240

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

start_time     = None
monitor_thread = None
idle_timer     = None
running        = False

# FIX #1 — Load DLL dari BASE_DIR
if not os.path.exists(dll_path):
    messagebox.showerror(
        "DLL Tidak Ditemukan",
        f"File usb_relay_device.dll tidak ditemukan di:\n{dll_path}\n\n"
        "Letakkan file DLL di folder yang sama dengan aplikasi ini."
    )
    raise SystemExit("DLL tidak ditemukan.")

usb_relay = ctypes.WinDLL(dll_path)
handle    = None
last_message       = None
device_map         = {}
relay_status_labels = []
VK_LBUTTON         = 0x01
_relay_warning_shown = False
is_fullscreen        = False

# ============================================================
# Struktur Windows
# ============================================================
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",          ctypes.wintypes.POINT),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.wintypes.ULONG),
    ]

class USBRelayDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("serial_number", ctypes.c_char_p),
        ("device_path",   ctypes.c_char_p),
        ("type",          ctypes.c_int),
        ("next",          ctypes.POINTER(ctypes.c_void_p)),
    ]

# ============================================================
# Definisi fungsi DLL
# ============================================================
usb_relay.usb_relay_init.argtypes = None
usb_relay.usb_relay_init.restype  = ctypes.c_int
usb_relay.usb_relay_device_enumerate.argtypes = None
usb_relay.usb_relay_device_enumerate.restype  = ctypes.POINTER(USBRelayDeviceInfo)
usb_relay.usb_relay_device_free_enumerate.argtypes = [ctypes.POINTER(USBRelayDeviceInfo)]
usb_relay.usb_relay_device_free_enumerate.restype  = None
usb_relay.usb_relay_device_open.argtypes  = [ctypes.POINTER(USBRelayDeviceInfo)]
usb_relay.usb_relay_device_open.restype   = ctypes.c_void_p
usb_relay.usb_relay_device_close.argtypes = [ctypes.c_void_p]
usb_relay.usb_relay_device_close.restype  = None
usb_relay.usb_relay_device_open_all_relay_channel.argtypes  = [ctypes.c_void_p]
usb_relay.usb_relay_device_open_all_relay_channel.restype   = ctypes.c_int
usb_relay.usb_relay_device_open_one_relay_channel.argtypes  = [ctypes.c_void_p, ctypes.c_int]
usb_relay.usb_relay_device_open_one_relay_channel.restype   = ctypes.c_int
usb_relay.usb_relay_device_close_one_relay_channel.argtypes = [ctypes.c_void_p, ctypes.c_int]
usb_relay.usb_relay_device_close_one_relay_channel.restype  = ctypes.c_int
usb_relay.usb_relay_exit.argtypes = None
usb_relay.usb_relay_exit.restype  = None

usb_relay.usb_relay_init()

# ============================================================
# DETEKSI KAMERA — cek apakah USB kamera benar-benar terdeteksi
# Windows setelah relay 1 dinyalakan. Membedakan: relay gagal vs
# kamera tidak menyala / kabel longgar.
# ============================================================
import subprocess

# Keyword nama kamera yang dicari di daftar PnP device Windows.
# Bisa diubah lewat GUI nanti (default mencakup model umum Canon DSLR)
camera_keyword = db_load_setting("camera_keyword", "Canon|EOS|1300D")

def get_pnp_device_names():
    """Ambil daftar nama semua PnP/USB device yang terpasang di Windows."""
    try:
        # CREATE_NO_WINDOW supaya tidak muncul jendela cmd berkedip
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        output = subprocess.check_output(
            ["wmic", "path", "Win32_PnPEntity", "get", "Caption"],
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            timeout=8
        )
        text = output.decode("utf-8", errors="ignore")
        names = [line.strip() for line in text.splitlines() if line.strip() and line.strip() != "Caption"]
        return names
    except Exception as e:
        print(f"[CameraCheck] Gagal ambil daftar device: {e}")
        return []

def is_camera_connected(keyword_pattern: str) -> bool:
    """True jika ada device yang namanya match salah satu keyword (pisah dengan '|')."""
    names = get_pnp_device_names()
    keywords = [k.strip().lower() for k in keyword_pattern.split("|") if k.strip()]
    if not keywords:
        return False
    for name in names:
        name_lower = name.lower()
        for kw in keywords:
            if kw in name_lower:
                return True
    return False

# Konfigurasi retry — bisa disesuaikan kalau perlu
CAMERA_RETRY_MAX           = 5    # maksimal percobaan restart APLIKASI
CAMERA_RETRY_WAIT_AFTER_ON = 6    # detik tunggu setelah relay ON sebelum cek

# Counter retry disimpan persisten di DB karena tiap restart = proses baru
# (variabel Python di memori akan hilang saat aplikasi benar-benar ditutup)
camera_retry_count = int(db_load_setting("camera_retry_count", "0"))

def _save_camera_retry_count(val: int):
    global camera_retry_count
    camera_retry_count = val
    threading.Thread(target=db_save_setting, args=("camera_retry_count", str(val)), daemon=True).start()

def restart_application():
    """Tutup aplikasi ini — batch wrapper (relay-start.bat) akan otomatis
    membuka ulang setelah 3 detik. Tidak perlu subprocess.Popen sama sekali."""
    log_message("🔄 Menutup aplikasi untuk restart otomatis via batch wrapper...")
    print("[Restart] Exit — relay-start.bat akan buka ulang dalam 3 detik.")
    root.after(500, shutdown_relay_and_exit)

def verify_camera_after_relay_on():
    """Tunggu beberapa detik setelah relay ON, lalu cek apakah kamera
    benar-benar muncul di daftar device Windows.

    Kalau kamera TIDAK terdeteksi: aplikasi akan DIRESTART SEPENUHNYA
    (proses ditutup total lalu dibuka ulang otomatis), diulang sampai
    CAMERA_RETRY_MAX kali (dihitung lintas restart lewat DB) atau sampai
    kamera akhirnya terdeteksi. Ini menangani kasus relay 'tidak ngangkat'
    kamera yang kadang terjadi acak (sekali beberapa jam)."""
    global _relay_1_on, camera_retry_count

    # Tunggu kamera boot setelah relay ON
    time.sleep(CAMERA_RETRY_WAIT_AFTER_ON)

    # Kalau di antara waktu tunggu relay sudah dimatikan manual, batalkan
    if not _relay_1_on:
        return

    detected = is_camera_connected(camera_keyword)

    if detected:
        if camera_retry_count > 0:
            log_message(f"✅ Kamera terdeteksi setelah {camera_retry_count}x restart aplikasi.")
            _save_camera_retry_count(0)   # reset counter, sukses
        else:
            log_message("✅ Kamera terdeteksi setelah relay ON (relay & kamera normal).")
        print("[CameraCheck] Kamera terdeteksi — relay & kamera normal.")
        return

    # ---- Kamera belum terdeteksi ----
    new_count = camera_retry_count + 1

    if new_count > CAMERA_RETRY_MAX:
        log_message(
            f"❌ Kamera TETAP TIDAK terdeteksi setelah {CAMERA_RETRY_MAX}x restart aplikasi. "
            f"Cek fisik kamera/kabel/USB."
        )
        print("[CameraCheck] GAGAL TOTAL: kamera tidak terdeteksi setelah semua retry restart.")
        _save_camera_retry_count(0)   # reset supaya retry berikutnya mulai dari 0 lagi
        return

    _save_camera_retry_count(new_count)
    log_message(f"⚠️ Kamera tidak terdeteksi. Merestart aplikasi (percobaan {new_count}/{CAMERA_RETRY_MAX})...")
    print(f"[CameraCheck] Restart aplikasi percobaan {new_count}/{CAMERA_RETRY_MAX}...")

    # Jalankan restart aplikasi penuh di main thread
    root.after(0, restart_application)

# ============================================================
# RELAY FUNCTIONS
# ============================================================
# Flag status relay 1 — cegah open/close dipanggil berulang
_relay_1_on = False

def open_relay_1(verify=True):
    """verify=True → setelah relay nyala, jalankan pengecekan kamera +
    auto-retry. verify=False dipakai internal saat proses retry supaya
    tidak memicu pengecekan bertumpuk berulang."""
    global handle, _relay_warning_shown, _relay_1_on
    if _relay_1_on:
        return
    if not handle:
        if not _relay_warning_shown:
            _relay_warning_shown = True
            messagebox.showerror("Peringatan", "Relay belom tersambung ke PC ! Silahkan Sambungkan dulu")
            refresh_device_list()
        return
    _relay_warning_shown = False
    result = usb_relay.usb_relay_device_open_one_relay_channel(handle, 1)
    if result == 0:
        _relay_1_on = True
        print("Relay 1 berhasil dinyalakan.")
        log_message("Success relay touch menyala.")
        if verify:
            # ---- Cek apakah kamera benar-benar terdeteksi setelah relay ON ----
            threading.Thread(target=verify_camera_after_relay_on, daemon=True).start()
    else:
        print("Gagal menyalakan relay 1.")
        messagebox.showerror("Error", "Failed to open relay 1")

def close_relay_1():
    global handle, _relay_1_on
    if not _relay_1_on:
        return   # sudah OFF, tidak perlu panggil DLL lagi
    if not handle:
        return
    result = usb_relay.usb_relay_device_close_one_relay_channel(handle, 1)
    if result == 0:
        _relay_1_on = False
        print("Relay 1 berhasil dimatikan.")
        log_message("Success relay touch 1 mati.")
    else:
        print("Gagal mematikan relay 1.")
        messagebox.showerror("Error", "Failed to close relay 1")

def open_relay_2():
    global handle
    if not handle:
        return
    result = usb_relay.usb_relay_device_open_one_relay_channel(handle, 2)
    if result == 0:
        print("Relay 2 berhasil dinyalakan.")
        log_message("Success relay 2 menyala.")
    else:
        print("Gagal menyalakan relay 2.")
        messagebox.showerror("Error", "Failed to open relay 2")

def close_relay_2():
    global handle
    if not handle:
        messagebox.showerror("Error", "Perangkat belum dibuka.")
        return
    result = usb_relay.usb_relay_device_close_one_relay_channel(handle, 2)
    if result == 0:
        print("Relay 2 berhasil dimatikan.")
        log_message("Success relay 2 mati.")
    else:
        print("Gagal mematikan relay 2.")
        messagebox.showerror("Error", "Failed to close relay 2")

# ============================================================
# SCREEN MONITORING
# ============================================================
def capture_screen():
    """Ambil screenshot lalu crop area jendela aplikasi ini supaya
    timer di GUI tidak terus-menerus memicu detect_change."""
    screenshot = pyautogui.screenshot()
    img = np.array(screenshot)

    # Coba exclude area jendela aplikasi (posisi & ukuran dari root)
    try:
        x  = root.winfo_rootx()
        y  = root.winfo_rooty()
        w  = root.winfo_width()
        h  = root.winfo_height()
        # Hitamkan (mask) area GUI agar tidak ikut dibandingkan
        img[y:y+h, x:x+w] = 0
    except Exception:
        pass  # root belum siap, abaikan

    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

def detect_change(prev_frame, new_frame, threshold=30):
    diff = cv2.absdiff(prev_frame, new_frame)
    _, thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    # Gunakan persentase pixel berubah (lebih robust dari sum mentah)
    changed_ratio = np.count_nonzero(thresh) / thresh.size
    return changed_ratio > 0.001   # minimal 0.1% layar berubah

def monitor_screen():
    global prev_frame, running
    prev_frame = capture_screen()
    last_change_time = time.time()
    relay_is_on = False   # tracking lokal supaya tidak double-call

    while running:
        time.sleep(1)
        new_frame = capture_screen()

        if detect_change(prev_frame, new_frame):
            last_change_time = time.time()
            if not relay_is_on:          # hanya nyalakan kalau belum ON
                open_relay_1()
                relay_is_on = True
        else:
            # Tidak ada perubahan — cek apakah sudah timeout
            if relay_is_on and (time.time() - last_change_time >= relay_timeout):
                close_relay_1()
                relay_is_on = False

        prev_frame = new_frame

# ============================================================
# HOOK / LISTENER
# ============================================================
def low_level_touch_proc(nCode, wParam, lParam):
    if nCode >= 0 and running:
        if wParam == WM_LBUTTONDOWN:
            print("Sentuhan atau klik mouse terdeteksi.")
            log_message("Touch Detected! Semua relay berhasil dinyalakan.")
            open_all_relay_channels()
            reset_idle_timer()
    return user32.CallNextHookEx(None, nCode, wParam, lParam)

def touch_listener1():
    global running
    running = True
    CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    touch_callback = CMPFUNC(low_level_touch_proc)
    hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, touch_callback, kernel32.GetModuleHandleW(None), 0)
    if hook_id == 0:
        print("Failed to install hook.")
        return
    print("Hook installed successfully.")
    msg = wintypes.MSG()
    while running:
        if user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnhookWindowsHookEx(hook_id)
    print("Hook uninstalled.")

def low_level_mouse_proc(nCode, wParam, lParam):
    global running
    if nCode == 0 and running:
        if wParam == WM_LBUTTONDOWN or wParam == WM_RBUTTONDOWN:
            mouse_struct = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            print(f"Klik terdeteksi di luar aplikasi: {mouse_struct.pt.x}, {mouse_struct.pt.y}")
            log_message("Touch Detected! Semua relay berhasil dinyalakan.")
            open_all_relay_channels()
            reset_idle_timer()
    return user32.CallNextHookEx(None, nCode, wParam, lParam)

def touch_listener():
    global running
    running = True
    CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
    mouse_callback = CMPFUNC(low_level_mouse_proc)
    hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, kernel32.GetModuleHandleW(None), 0)
    msg = ctypes.wintypes.MSG()
    while running:
        user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
    user32.UnhookWindowsHookEx(hook_id)

def mouse_listener():
    global running
    running = True
    CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
    mouse_callback = CMPFUNC(low_level_mouse_proc)
    hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_callback, kernel32.GetModuleHandleW(None), 0)
    msg = ctypes.wintypes.MSG()
    while running:
        user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
    user32.UnhookWindowsHookEx(hook_id)

# ============================================================
# START / STOP FUNCTIONS
# ============================================================
def mulai_function():
    global running, monitor_thread, start_time
    if running:
        print("Monitoring sudah berjalan!")
        return
    running = True
    start_time = time.time()
    print("Monitoring layar dimulai...")
    monitor_thread = threading.Thread(target=monitor_screen, daemon=True)
    monitor_thread.start()
    update_timer()

def hentikan_function():
    global running
    if not running:
        print("Monitoring sudah berhenti!")
        return
    running = False
    timer_label.config(text="⏱ 00:00:00")
    def stop_thread():
        if monitor_thread and monitor_thread.is_alive():
            monitor_thread.join(timeout=1)
        close_relay_1()
        print("Monitoring layar dihentikan.")
    threading.Thread(target=stop_thread, daemon=True).start()

def save_relay_time():
    """Simpan relay_timeout ke variabel global DAN ke SQLite."""
    global relay_timeout
    try:
        new_val = int(entry_waktu.get())
        if new_val <= 0:
            raise ValueError("Harus lebih dari 0")
        relay_timeout = new_val
        # ---- FIX PERBAIKAN 1: simpan ke DB supaya persisten ----
        threading.Thread(
            target=db_save_setting,
            args=("relay_timeout", str(relay_timeout)),
            daemon=True
        ).start()
        log_message(f"Waktu relay diatur ke {relay_timeout} detik (tersimpan)")
        print(f"Waktu relay diatur ke {relay_timeout} detik")
    except ValueError:
        messagebox.showwarning("Input Salah", "Masukkan angka bulat positif yang valid!")

def save_camera_keyword():
    """Simpan keyword nama kamera (pisah dengan '|') ke SQLite."""
    global camera_keyword
    new_val = entry_camera_keyword.get().strip()
    if not new_val:
        messagebox.showwarning("Input Salah", "Keyword tidak boleh kosong!")
        return
    camera_keyword = new_val
    threading.Thread(
        target=db_save_setting,
        args=("camera_keyword", camera_keyword),
        daemon=True
    ).start()
    log_message(f"Keyword kamera diatur ke '{camera_keyword}' (tersimpan)")

prev_frame = None

def start_function():
    global running, start_time
    running = True
    print("Mode Start diaktifkan. Klik mouse akan menyalakan relay.")
    thread = threading.Thread(target=mouse_listener, daemon=True)
    thread.start()
    reset_idle_timer()
    start_time = time.time()
    update_timer()

def stop_function():
    global running, idle_timer
    running = False
    print("Mode Start dihentikan.")
    root.unbind("<Button>")
    if idle_timer:
        idle_timer.cancel()
    close_all_relay_channels()
    timer_label.config(text="⏱ 00:00:00")

# ============================================================
# LOG MESSAGE — tampil di listbox + simpan ke SQLite
# ============================================================
def log_message(message):
    global last_message
    current_time      = time.strftime("%H:%M:%S")
    formatted_message = f"[{current_time}] {message}"
    if last_message == formatted_message:
        return
    if log_listbox.size() > 0:
        log_listbox.delete(tk.END)
    log_listbox.insert(tk.END, formatted_message)
    log_listbox.yview(tk.END)
    last_message = formatted_message
    threading.Thread(target=db_insert_log, args=(message,), daemon=True).start()

def update_listbox(message):
    global last_message
    if last_message == message:
        return
    if log_listbox.size() > 0:
        log_listbox.delete(tk.END)
    log_listbox.insert(tk.END, message)
    log_listbox.yview(tk.END)
    last_message = message

def reset_idle_timer():
    global idle_timer
    if idle_timer:
        idle_timer.cancel()
    idle_timer = threading.Timer(300, close_all_relay_channels)
    idle_timer.start()

# ============================================================
# DEVICE FUNCTIONS
# ============================================================
def detect_relay_devices():
    device_list = usb_relay.usb_relay_device_enumerate()
    if not device_list:
        print("Tidak ada perangkat yang terdeteksi.")
        return None
    devices = []
    current_device = device_list
    while current_device:
        device_info = {
            "serial_number": current_device.contents.serial_number.decode("utf-8"),
            "device_path":   current_device.contents.device_path.decode("utf-8"),
            "type":          current_device.contents.type,
        }
        devices.append(device_info)
        print(f"Perangkat terdeteksi: {device_info}")
        current_device = current_device.contents.next
    usb_relay.usb_relay_device_free_enumerate(device_list)
    return devices

def close_all_relay_channels():
    global handle
    if not handle:
        print("Perangkat belum dibuka. Tidak ada relay yang perlu dimatikan.")
        return
    result = usb_relay.usb_relay_device_close_all_relay_channel(handle)
    if result == 0:
        print("Semua relay berhasil dimatikan.")
        log_message("Succes relay mati.")
        for label in relay_status_labels:
            label.config(bg="gray")
    else:
        print("Gagal mematikan semua relay.")
        messagebox.showerror("Error", "Failed to close all relay channels")

def open_relay_device_automatically():
    global handle
    devices = detect_relay_devices()
    if not devices:
        messagebox.showerror("Error", "Tidak ada perangkat relay yang terdeteksi.")
        return None
    selected_serial = devices[0]["serial_number"]
    device_info = USBRelayDeviceInfo(
        serial_number=devices[0]["serial_number"].encode('utf-8'),
        device_path=devices[0]["device_path"].encode('utf-8'),
        type=devices[0]["type"],
        next=None
    )
    print(f"Membuka perangkat dengan serial: {selected_serial}")
    handle = usb_relay.usb_relay_device_open(ctypes.byref(device_info))
    if not handle:
        print("Gagal membuka perangkat. Handle tidak valid.")
        messagebox.showerror("Error", "Open Device Error!!")
        return None
    print("Perangkat berhasil dibuka.")
    return handle

def open_all_relay_channels():
    global handle
    if not handle:
        messagebox.showerror("Error", "Perangkat belum dibuka.")
        return
    result = usb_relay.usb_relay_device_open_all_relay_channel(handle)
    if result == 0:
        print("Semua relay berhasil dinyalakan.")
        log_message("Success relay menyala.")
        for label in relay_status_labels:
            label.config(bg="red")
    else:
        print("Gagal menyalakan semua relay.")
        messagebox.showerror("Error", "Failed to open all relay channels")

def refresh_device_list():
    global handle
    device_map.clear()
    device_listbox.delete(0, tk.END)
    devices = detect_relay_devices()
    if devices:
        for device in devices:
            device_map[device["serial_number"]] = device
            device_listbox.insert(tk.END, device["serial_number"])
        handle = open_relay_device_automatically()
        if handle:
            open_status_label.config(text="✅ Device Opened", bootstyle="success")
    else:
        device_label.config(text="Tidak ada perangkat relay yang terdeteksi.")
        open_status_label.config(text="✅ Device Opened", bootstyle="success")

# ============================================================
# FIX #3 — Shutdown: matikan relay lalu tutup aplikasi
# Guard flag supaya tidak dipanggil dua kali (atexit + mainloop)
# ============================================================
_shutdown_called = False

def shutdown_relay_and_exit():
    global handle, running, idle_timer, _shutdown_called
    # ---- Cegah double-call yang menyebabkan access violation ----
    if _shutdown_called:
        print("[Shutdown] Sudah dipanggil sebelumnya, diabaikan.")
        return
    _shutdown_called = True

    print("[Shutdown] Mematikan relay dan menutup aplikasi...")
    running = False
    if idle_timer:
        idle_timer.cancel()
        idle_timer = None
    if handle:
        _h = handle
        handle = None          # set None dulu sebelum memanggil DLL
        try:
            usb_relay.usb_relay_device_close_one_relay_channel(_h, 1)
            usb_relay.usb_relay_device_close_one_relay_channel(_h, 2)
            print("[Shutdown] Relay 1 & 2 dimatikan.")
        except Exception as e:
            print(f"[Shutdown] Error saat matikan relay: {e}")
        try:
            usb_relay.usb_relay_device_close(_h)
        except Exception as e:
            print(f"[Shutdown] Error saat tutup handle: {e}")
    try:
        usb_relay.usb_relay_exit()
    except Exception:
        pass
    # ---- Tutup handle mutex supaya proses baru (saat restart) bisa
    # langsung dapat mutex tanpa menunggu OS membersihkannya ----
    try:
        if _mutex:
            ctypes.windll.kernel32.CloseHandle(_mutex)
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    # ---- Pastikan proses benar-benar terminate (penting untuk restart) ----
    os._exit(0)

def on_closing():
    """Tombol X jendela → sembunyikan ke tray."""
    hide_window()

def on_closing_exit():
    shutdown_relay_and_exit()

# ============================================================
# TIMER
# ============================================================
def update_timer():
    global start_time
    if running and start_time is not None:
        elapsed = time.time() - start_time
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        timer_label.config(text=f"⏱ {h:02}:{m:02}:{s:02}")
        root.after(1000, update_timer)

# ============================================================
# FULLSCREEN TOGGLE
# ============================================================
def toggle_fullscreen():
    global is_fullscreen
    is_fullscreen = not is_fullscreen
    root.attributes("-fullscreen", is_fullscreen)
    btn_fullscreen.config(text="⛶ Windowed" if is_fullscreen else "⛶ Fullscreen")

def escape_fullscreen(event=None):
    global is_fullscreen
    if is_fullscreen:
        is_fullscreen = False
        root.attributes("-fullscreen", False)
        btn_fullscreen.config(text="⛶ Fullscreen")

# ============================================================
# FIX PERBAIKAN 2 — TRAY ICON lebih terang & keren
# Desain: latar hitam glossy, lingkaran gradien hijau-cyan,
# simbol petir kuning di tengah (identik relay/listrik)
# ============================================================
def create_tray_image():
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background bulat gelap dengan border neon cyan
    cx, cy, r = size // 2, size // 2, size // 2 - 2
    # Shadow/glow effect: lingkaran luar transparan
    for glow_r in range(r + 4, r - 1, -1):
        alpha = int(180 * (1 - (glow_r - r + 1) / 6)) if glow_r > r else 0
        glow_color = (0, 255, 220, alpha)
        draw.ellipse(
            [cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r],
            outline=glow_color, width=1
        )

    # Lingkaran latar belakang hitam pekat
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(18, 18, 28, 255))

    # Ring luar neon cyan tebal
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 outline=(0, 230, 255, 255), width=3)

    # Ring dalam lebih tipis warna hijau-lime
    r2 = r - 5
    draw.ellipse([cx - r2, cy - r2, cx + r2, cy + r2],
                 outline=(50, 255, 120, 180), width=1)

    # Simbol petir (⚡) — poligon kuning-oranye di tengah
    # Koordinat petir relatif ke center
    bolt = [
        (cx + 4,  cy - 18),   # ujung atas kanan
        (cx - 2,  cy - 2),    # tengah kiri
        (cx + 5,  cy - 2),    # tengah kanan (lebih lebar)
        (cx - 4,  cy + 18),   # ujung bawah kiri
        (cx + 2,  cy + 2),    # tengah bawah kiri
        (cx - 5,  cy + 2),    # tengah bawah kanan
    ]
    draw.polygon(bolt, fill=(255, 220, 0, 255))       # isi kuning cerah
    draw.polygon(bolt, outline=(255, 160, 0, 255))    # outline oranye

    # Titik kecil di sudut kanan bawah sebagai indikator "active" (hijau)
    dot_x, dot_y, dot_r = cx + r - 9, cy + r - 9, 5
    draw.ellipse(
        [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
        fill=(0, 255, 100, 255), outline=(255, 255, 255, 200), width=1
    )

    return img

tray_icon     = None
_tray_running = False   # guard: pastikan hanya 1 tray icon aktif

def create_tray_icon():
    global tray_icon, _tray_running
    image = create_tray_image()
    menu  = Menu(
        MenuItem("Tampilkan", show_window),
        MenuItem("Keluar",    exit_from_tray),
    )
    tray_icon = Icon("app", image, "URC Touch Relay", menu)
    _tray_running = True
    tray_icon.run()          # blocking sampai tray_icon.stop() dipanggil
    _tray_running = False

def hide_window():
    global _tray_running
    # Jangan buat tray baru kalau sudah ada yang berjalan
    if _tray_running:
        print("[Tray] Sudah ada tray icon aktif, tidak membuat duplikat.")
        root.withdraw()
        return
    root.withdraw()
    threading.Thread(target=create_tray_icon, daemon=True).start()

def show_window(icon, item):
    root.after(0, root.deiconify)
    if tray_icon:
        tray_icon.stop()

def exit_from_tray(icon, item):
    if tray_icon:
        tray_icon.stop()
    root.after(0, shutdown_relay_and_exit)

def on_startup():
    root.after(10000, hide_window)

# ============================================================
# FIX #2 — Toggle Auto Startup
# ============================================================
def toggle_auto_startup():
    if startup_var.get():
        register_startup()
        print("[Startup] Auto startup diaktifkan.")
    else:
        unregister_startup()
        print("[Startup] Auto startup dinonaktifkan.")

# ============================================================
# GUI
# ============================================================
root = tb.Window(themename="darkly")
root.title("URC Touch Relay")
root.geometry("480x420")
root.resizable(True, True)

root.protocol("WM_DELETE_WINDOW", on_closing)
root.bind("<Escape>", escape_fullscreen)

frame = ttk.Frame(root, padding=8)
frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

# ---- Timer ----
timer_label = ttk.Label(
    frame, text="⏱ 00:00:00",
    font=("Arial", 14, "bold"),
    bootstyle="info", relief="raised", padding=8,
    background="#17a2b8", foreground="white", anchor="center"
)
timer_label.pack(fill=tk.X, pady=(0, 6))

# Dummy label agar kode lama tidak error
status_label  = tk.Label(frame)
log_area_var  = tk.StringVar()

# ---- Log listbox ----
log_listbox = tk.Listbox(frame, height=2, width=40)
log_listbox.pack(padx=5, pady=4, fill=tk.X)

# ---- Input Waktu Relay ----
frame_input = ttk.Frame(frame)
frame_input.pack(pady=3)
ttk.Label(frame_input, text="Waktu Perubahan Layar (detik):").pack(side="left", padx=4)
entry_waktu = ttk.Entry(frame_input, width=8)
entry_waktu.pack(side="left", padx=4)
# ---- FIX PERBAIKAN 1: isi entry dari nilai yang dimuat dari DB ----
entry_waktu.insert(0, str(relay_timeout))
ttk.Button(frame_input, text="💾 Save", command=save_relay_time).pack(side="left", padx=4)

# ---- Input Keyword Kamera (untuk deteksi USB) ----
frame_camera = ttk.Frame(frame)
frame_camera.pack(pady=3)
ttk.Label(frame_camera, text="Keyword Kamera (pisah '|'):").pack(side="left", padx=4)
entry_camera_keyword = ttk.Entry(frame_camera, width=18)
entry_camera_keyword.pack(side="left", padx=4)
entry_camera_keyword.insert(0, camera_keyword)
ttk.Button(frame_camera, text="💾 Save", command=save_camera_keyword).pack(side="left", padx=4)

# ---- Device section ----
device_label = ttk.Label(frame, text="🔍 Mendeteksi perangkat", font=("Arial", 10, "bold"))
device_label.pack(pady=3, anchor="w")

device_listbox = tk.Listbox(frame, height=2, font=("Arial", 9))
device_listbox.pack(pady=3, fill=tk.X)

open_status_label = ttk.Label(frame)  # hidden

# ---- FIX #2 — Checkbox Auto Startup ----
startup_var = tk.BooleanVar(value=is_startup_registered())
chk_startup = ttk.Checkbutton(
    frame,
    text="🚀 Auto Start saat Windows Boot",
    variable=startup_var,
    command=toggle_auto_startup,
    bootstyle="success-round-toggle",
)
chk_startup.pack(anchor="w", padx=10, pady=(2, 4))

# ---- Tombol Start / Stop ----
button_frame = ttk.Frame(frame)
button_frame.pack(fill=tk.X, pady=4)

btn_start = tb.Button(button_frame, text="▶ Start", bootstyle="primary", command=mulai_function)
btn_stop  = tb.Button(button_frame, text="⏹ Stop",  bootstyle="warning", command=hentikan_function)

btn_start.grid(row=0, column=0, padx=10, pady=8, sticky="ew", ipadx=20, ipady=14)
btn_stop.grid( row=0, column=1, padx=10, pady=8, sticky="ew", ipadx=20, ipady=14)

for i in range(2):
    button_frame.columnconfigure(i, weight=1)

# ---- Tombol Fullscreen ----
btn_fullscreen = tb.Button(frame, text="⛶ Fullscreen",
                            bootstyle="secondary", command=toggle_fullscreen)
btn_fullscreen.pack(fill=tk.X, padx=10, pady=4, ipady=6)

# ============================================================
# STARTUP
# ============================================================
root.after(100, on_startup)
refresh_device_list()
mulai_function()
open_relay_2()

atexit.register(shutdown_relay_and_exit)
signal.signal(signal.SIGTERM, lambda signum, frame: shutdown_relay_and_exit())

root.mainloop()