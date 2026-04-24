#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Criptează fișierul .env în env.dpapi (compatibil cu PyInstaller executabile).
Folosim DPAPI (Data Protection API) prin ctypes — NU necesită pywin32.
Rezultatul env.dpapi se poate decripta doar pe același cont Windows.
"""

import ctypes
import ctypes.wintypes as wt
from pathlib import Path

# Fișiere sursă / destinație
ENV_PATH = Path(".env")
OUT_PATH = Path("env.dpapi")

if not ENV_PATH.exists():
    raise FileNotFoundError("⚠️  Fișierul .env nu există în folderul curent.")

# --- Structuri și funcții DPAPI ---
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]

def _blob_from_bytes(data: bytes) -> DATA_BLOB:
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))

def dpapi_protect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob = _blob_from_bytes(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        result = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
    return result

# --- Criptare ---
plain = ENV_PATH.read_bytes()
cipher = dpapi_protect(plain)
OUT_PATH.write_bytes(cipher)
print(f"✅ Fișier criptat: {OUT_PATH} (legat de userul Windows curent)")

# --- Ștergere opțională .env ---
wipe = True
if wipe:
    ENV_PATH.write_bytes(b"")
    ENV_PATH.unlink(missing_ok=True)
    print("🧹 Fișier .env șters după criptare.")
