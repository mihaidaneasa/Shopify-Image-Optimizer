#!/usr/bin/env python3
"""
Optimizare imagini pentru PRODUSE (Shopify Admin GraphQL 2025-10)
— calitate WebP 92, confirmare LIVE/DRY-RUN, fără raport CSV —

Ce face:
  • Citește TOATE imaginile legate de produse (product.images + variant.image + featuredMedia/media)
    folosind operații Bulk.
  • Descarcă doar imaginile care NU sunt deja .webp (implicit skip .webp).
  • Convertește în WEBP (quality default 92), urcă în Shopify și atașează la produs.
  • După atașare, șterge imaginile vechi non-webp înlocuite (doar în modul LIVE).
  • Salvează copia locală WEBP într-un dosar ales prin popup (--gui) sau prin --out.
  • Popup sumar (dacă Tk e disponibil).
  • Fereastră separată de control cu STOP + logo (dacă există „Logo SEO.png”).

Dependențe:
  pip install requests pillow python-dotenv
"""

import argparse, json, os, re, sys, time, requests, tempfile, shutil
from dataclasses import dataclass
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, UnidentifiedImageError
from dotenv import load_dotenv
from typing import List, Optional, Tuple, Dict, Set

# ── .env prin DPAPI (ctypes) — nu necesită pywin32
import ctypes, ctypes.wintypes as wt, pathlib

BASE = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).parent))

# 1) .env local pentru DEV (nu suprascrie ce vine din env.dpapi)
load_dotenv(BASE / ".env", override=False)

# 2) env.dpapi pentru PROD (suprascrie valorile)
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

def _blob_from_bytes(b: bytes) -> DATA_BLOB:
    buf = ctypes.create_string_buffer(b)
    return DATA_BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))

def dpapi_unprotect(cipher: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    inb = _blob_from_bytes(cipher); outb = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(outb)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outb.pbData, outb.cbData)
    finally:
        kernel32.LocalFree(outb.pbData)

enc_path = BASE / "env.dpapi"
if enc_path.exists():
    try:
        dec = dpapi_unprotect(enc_path.read_bytes())
        tmp = BASE / ".env.dec"
        tmp.write_bytes(dec)
        load_dotenv(tmp, override=True)
    finally:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass

# ---- POPUP (tkinter) ----
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False
    tk = None  # type: ignore

# ---- Dialog YES/NO cu fallback (tk → Windows MessageBox → consolă)
def ask_yes_no(title: str, message: str, default_no: bool = True) -> bool:
    # 1) Tkinter
    if TK_AVAILABLE:
        root = tk.Tk(); root.withdraw()  # type: ignore
        try:
            return messagebox.askyesno(title, message)  # type: ignore
        finally:
            try: root.destroy()  # type: ignore
            except Exception: pass
    # 2) Windows MessageBox
    try:
        import ctypes
        MB_YESNO = 0x00000004
        MB_ICONQUESTION = 0x00000020
        res = ctypes.windll.user32.MessageBoxW(None, message, title, MB_YESNO | MB_ICONQUESTION)  # type: ignore
        return res == 6  # IDYES
    except Exception:
        pass
    # 3) Consolă
    try:
        ans = input(f"{title}\n{message}\n[yes/no]: ").strip().lower()
        return ans in ("y", "yes", "da")
    except Exception:
        return not default_no

# ---- ImageTk pentru logo (dacă e disponibil) ----
try:
    from PIL import ImageTk  # type: ignore
except Exception:
    ImageTk = None  # type: ignore

# ---- Căutare & încărcare logo pentru GUI ----
def find_logo_path() -> Optional[Path]:
    candidates = ["Logo SEO.png", "logo.png", "Logo.png", "ivesa_logo.png", "ivesa.png"]
    search_roots: list[Path] = []

    # 0) PyInstaller runtime (_MEIPASS)
    try:
        import sys as _sys
        if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
            search_roots.append(Path(_sys._MEIPASS))  # type: ignore[attr-defined]
    except Exception:
        pass

    # 1) Lângă exe / lângă script
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        search_roots.append(base)
    except Exception:
        pass

    # 2) Current Working Directory
    search_roots.append(Path.cwd())

    for root in search_roots:
        for name in candidates:
            p = root / name
            if p.exists():
                return p
    return None

def load_logo_tk(max_w: int = 180, max_h: int = 90):
    if not (TK_AVAILABLE and ImageTk):
        return None, None
    path = find_logo_path()
    if not path:
        return None, None
    try:
        im = Image.open(path); im.load(); im = im.convert("RGBA")
        w, h = im.size
        scale = min(max_w / w, max_h / h, 1.0)
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        ph = ImageTk.PhotoImage(im)  # type: ignore
        return ph, im
    except Exception:
        return None, None

# ----------------- ENV / CONFIG -----------------
SHOP = os.getenv("SHOP_DOMAIN")
TOKEN = os.getenv("ADMIN_TOKEN")
if not SHOP or not TOKEN:
    raise SystemExit("Setează .env cu SHOP_DOMAIN și ADMIN_TOKEN")

API = f"https://{SHOP}/admin/api/2025-10/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN}
IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

# ----------------- MODELS -----------------
@dataclass
class ProdImage:
    product_id: str
    product_title: str
    image_id: str           # GID sau "bodyHtml"
    alt_text: Optional[str]
    url: str                # URL sursă

# ----------------- UI HELPERS -----------------
def pick_out_dir(default_out: str) -> Path:
    if not TK_AVAILABLE:
        return Path(default_out).expanduser().resolve()
    root = tk.Tk(); root.withdraw()  # type: ignore
    chosen = filedialog.askdirectory(title="Alege dosarul TINTĂ (copii WEBP)")  # type: ignore
    root.destroy()  # type: ignore
    if not chosen:
        raise SystemExit("Ai anulat alegerea dosarului. Rulează din nou cu --gui sau setează --out \"C:\\cale\\\".")
    return Path(chosen).resolve()

def show_summary_popup(found:int, converted:int, skipped_webp:int, errors:int, dry:int):
    if not TK_AVAILABLE:
        return
    root = tk.Tk(); root.withdraw()  # type: ignore
    msg = (
        f"Găsite (după filtrare): {found}\n"
        f"Convertite (OK): {converted}\n"
        f"Sărite WEBP: {skipped_webp}\n"
        f"Dry-run: {dry}\n"
        f"Erori: {errors}"
    )
    try:
        messagebox.showinfo("Rezumat optimizare", msg)  # type: ignore
    finally:
        root.destroy()  # type: ignore

# ----- UI de control (Stop) pe același thread -----
class ControlWindow:
    def __init__(self):
        self.stopped = False
        if not TK_AVAILABLE:
            self.root = None
            self.status_var = None
            return
        self.root = tk.Tk()  # type: ignore
        self.root.title("Optimizare imagini – Control")  # type: ignore
        self.root.geometry("420x180")  # type: ignore

        # Logo
        self._logo_ref = None
        ph, _ = load_logo_tk()
        if ph:
            self._logo_ref = ph
            try:
                self.root.iconphoto(True, ph)
            except Exception:
                pass
            tk.Label(self.root, image=ph, pady=4).pack(pady=(10, 2))

        # Status
        self.status_var = tk.StringVar(value="Pregătire…")  # type: ignore
        tk.Label(self.root, textvariable=self.status_var, padx=10, pady=10, anchor="w").pack(fill="x")  # type: ignore

        # STOP
        btn = tk.Button(self.root, text="STOP", font=("Segoe UI", 12, "bold"),
                        bg="#C62828", fg="white", command=self.stop)  # type: ignore
        btn.pack(pady=10, ipadx=10, ipady=4)  # type: ignore
        self.root.protocol("WM_DELETE_WINDOW", self.stop)  # type: ignore

    def stop(self):
        if getattr(self, "status_var", None):
            self.status_var.set("Oprit. Se finalizează în siguranță…")
        self.stopped = True

def start_control_ui_mainthread() -> Optional[ControlWindow]:
    if not TK_AVAILABLE:
        return None
    return ControlWindow()

def ui_pump(control: Optional[ControlWindow]):
    if TK_AVAILABLE and control and getattr(control, "root", None):
        try:
            control.root.update_idletasks()  # type: ignore
            control.root.update()            # type: ignore
        except Exception:
            pass

# ----------------- HTTP -----------------
def session_with_retries() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=5, connect=5, read=5, backoff_factor=0.6,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset(["GET", "POST"]))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s

# ----------------- GRAPHQL -----------------
def gql(query: str, variables: Optional[dict] = None) -> dict:
    r = requests.post(API, headers=HEADERS, json={"query": query, "variables": variables or {}}, timeout=180)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(str(data["errors"]))
    return data["data"]

# ---------- BULK HELPERS ----------
def cancel_if_running():
    try:
        gql("mutation { bulkOperationCancel { userErrors { message } } }")
    except Exception:
        pass

def start_bulk_images() -> str:
    """Job #1: products + images (legacy)"""
    inner = """
    { products(first: 250) {
        edges { node {
          __typename id title bodyHtml
          images(first: 250) { edges { node { __typename id altText url } } }
          featuredMedia { __typename ... on MediaImage { id image { __typename id altText url } } }
        }}
      }
    }""".strip()
    mut = f"""
    mutation {{
      bulkOperationRunQuery(query: {json.dumps(inner)}) {{
        bulkOperation {{ id status }}
        userErrors {{ field message }}
      }}
    }}"""
    data = gql(mut)["bulkOperationRunQuery"]
    errs = data.get("userErrors") or []
    if errs:
        raise RuntimeError("Bulk start failed (images): " + "; ".join(e.get("message", "") for e in errs))
    op_id = data["bulkOperation"]["id"]
    print(f"[DEBUG] Started Bulk (images) ID: {op_id}")
    return op_id

def start_bulk_media_and_variants() -> str:
    """Job #2: products + media + variants (max ~5 conexiuni)"""
    inner = """
    { products(first: 250) {
        edges { node {
          __typename id title bodyHtml
          media(first: 250) { edges { node { __typename ... on MediaImage { id image { __typename id altText url } } } } }
          variants(first: 250) { edges { node { __typename id image { __typename id altText url } } } }
        }}
      }
    }""".strip()
    mut = f"""
    mutation {{
      bulkOperationRunQuery(query: {json.dumps(inner)}) {{
        bulkOperation {{ id status }}
        userErrors {{ field message }}
      }}
    }}"""
    data = gql(mut)["bulkOperationRunQuery"]
    errs = data.get("userErrors") or []
    if errs:
        raise RuntimeError("Bulk start failed (media/variants): " + "; ".join(e.get("message", "") for e in errs))
    op_id = data["bulkOperation"]["id"]
    print(f"[DEBUG] Started Bulk (media/variants) ID: {op_id}")
    return op_id

def poll_bulk(expected_id: str, control: Optional[ControlWindow]) -> dict:
    q = "query { currentBulkOperation { id status url errorCode } }"
    while True:
        cur = gql(q)["currentBulkOperation"]
        ui_pump(control)
        if control and control.stopped:
            print("[STOP] Oprit de utilizator în timpul Bulk.")
            return {"id": expected_id, "status": "CANCELED", "url": None, "errorCode": "USER_STOP"}
        if cur and cur["id"] == expected_id and cur["status"] in ("COMPLETED", "FAILED", "CANCELED"):
            print(f"[DEBUG] Bulk {cur['id']} → {cur['status']}")
            return cur
        time.sleep(0.25)
        ui_pump(control)

def download_bulk_ndjson(url: str, sess: requests.Session, control: Optional[ControlWindow]) -> List[str]:
    res = sess.get(url, timeout=600)
    res.raise_for_status()
    text = res.text.strip()
    ui_pump(control)
    return text.splitlines() if text else []

# ----------------- PARSARE NDJSON -----------------
def stem_and_ext(u: str) -> tuple[str, str]:
    base = u.split('?', 1)[0]
    name = os.path.basename(urlsplit(base).path)
    stem, ext = os.path.splitext(name)
    return stem.lower(), ext.lower()

def is_webp_url(u: str) -> bool:
    return urlsplit(u.split("?", 1)[0]).path.lower().endswith(".webp")

def original_filename_from_url(u: str) -> str:
    """
    Extrage numele original al fișierului din URL.
    Ex: .../produse/geanta_neagra.png?v=123 → geanta_neagra.png
    """
    base = u.split("?", 1)[0]
    path = urlsplit(base).path
    return os.path.basename(path)

def iter_bulk_items(lines: List[str]) -> List[ProdImage]:
    parent_of: dict[str, str] = {}
    type_of: dict[str, str] = {}
    prod_title: dict[str, str] = {}
    images: List[ProdImage] = []

    for ln in lines:
        o = json.loads(ln)
        t = o.get("__typename")
        oid = o.get("id")

        if oid:
            type_of[oid] = t

        parent_id = o.get("__parentId")
        if oid and parent_id:
            parent_of[oid] = parent_id

        # Product + bodyHtml (poze din descriere)
        if t == "Product" and oid:
            title = o.get("title", "")
            prod_title[oid] = title
            body = o.get("bodyHtml") or ""
            for src in IMG_RE.findall(body):
                images.append(ProdImage(oid, title, "bodyHtml", None, src))

    # a doua trecere: toate nodurile Image, le legăm de produsul părinte
    for ln in lines:
        o = json.loads(ln)
        t = o.get("__typename")
        oid = o.get("id")
        url = o.get("url")

        if t != "Image" or not oid or not url:
            continue

        pid = oid
        seen = set()
        product_id = None

        while pid and pid not in seen:
            par = parent_of.get(pid)
            if not par:
                break
            if type_of.get(par) == "Product":
                product_id = par
                break
            seen.add(pid)
            pid = par

        if product_id and product_id in prod_title:
            images.append(
                ProdImage(product_id, prod_title[product_id], oid, o.get("altText"), url)
            )

    return images

# ----------------- SHOPIFY MEDIA OPS -----------------
def staged_upload_local_file(local_path: Path, sess: requests.Session, mime: str = "image/webp") -> str:
    q = """
    mutation($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets { url resourceUrl parameters { name value } }
        userErrors { field message }
      }
    }"""
    payload = {"input": [{"resource": "FILE", "filename": local_path.name, "mimeType": mime, "httpMethod": "POST"}]}
    data = gql(q, payload)
    t = data["stagedUploadsCreate"]["stagedTargets"][0]
    post_url = t["url"]
    params = {p["name"]: p["value"] for p in t["parameters"]}
    with open(local_path, "rb") as fh:
        files = {"file": (local_path.name, fh, mime)}
        up = sess.post(post_url, data=params, files=files, timeout=600)
        up.raise_for_status()
    return t["resourceUrl"]

def attach_media_to_product(product_gid: str, original_src_url: str, alt: Optional[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
    q = """
    mutation addMedia($productId: ID!, $media: [CreateMediaInput!]!) {
      productCreateMedia(productId: $productId, media: $media) {
        media {
          __typename
          ... on MediaImage { id preview { image { url } } alt status }
        }
        mediaUserErrors { message }
      }
    }"""
    variables = {
        "productId": product_gid,
        "media": [{"alt": alt or "", "mediaContentType": "IMAGE", "originalSource": original_src_url}],
    }
    data = gql(q, variables)["productCreateMedia"]
    errs = [e["message"] for e in (data.get("mediaUserErrors") or [])]
    media = (data.get("media") or [])
    if media:
        m = media[0]
        mid = m.get("id")
        purl = ((m.get("preview") or {}).get("image") or {}).get("url")
        return mid, purl, errs
    return None, None, errs

def list_media_and_variant_urls(product_id: str):
    """
    Returnează:
      - o listă de dict-uri: {"id": mediaId, "url": url_imagine_media}
      - un set de URL-uri (fără query) folosite de variante
    """
    q = """
    query($id: ID!) {
      product(id: $id) {
        media(first: 250) {
          edges {
            node {
              __typename
              ... on MediaImage {
                id
                image { url }
              }
            }
          }
        }
        variants(first: 250) {
          edges {
            node {
              id
              image { url }
            }
          }
        }
      }
    }"""
    data = gql(q, {"id": product_id})["product"]

    media_rows = []
    for e in data["media"]["edges"]:
        n = e["node"]
        if n["__typename"] == "MediaImage":
            url = (n.get("image") or {}).get("url") or ""
            media_rows.append({"id": n["id"], "url": url})

    variant_urls: set[str] = set()
    for e in data["variants"]["edges"]:
        node = e["node"]
        img = node.get("image")
        if img and img.get("url"):
            base = img["url"].split("?", 1)[0]
            variant_urls.add(base)

    return media_rows, variant_urls

def delete_media(product_id: str, media_ids: List[str]):
    if not media_ids:
        return
    q = """
    mutation($productId: ID!, $mediaIds: [ID!]!) {
      productDeleteMedia(productId: $productId, mediaIds: $mediaIds) {
        deletedMediaIds
        mediaUserErrors { message }
      }
    }"""
    return gql(q, {"productId": product_id, "mediaIds": media_ids})

# ----------------- DOWNLOAD + CONVERT -----------------
def download_convert_webp(src_url: str, dst_path: Path, quality: int, sess: requests.Session) -> Path:
    base = src_url.split("?", 1)[0]
    src = f"{base}?width=2048"
    r = sess.get(src, timeout=180)
    r.raise_for_status()
    content = r.content
    try:
        with Image.open(BytesIO(content)) as im:
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst_path, "WEBP", method=6, quality=quality, optimize=True)
            return dst_path
    except UnidentifiedImageError:
        raise RuntimeError("UNSUPPORTED_FORMAT")

# ----------------- MAIN -----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = fără limită (implicit toate imaginile)")
    ap.add_argument("--quality", type=int, default=92, help="Calitate WebP (60–100 recomandat)")
    ap.add_argument("--out", default="out_images", help="Folder local pentru copiile WEBP")
    ap.add_argument("--gui", action="store_true", help="Alege dosarul TINTĂ prin popup (tkinter)")
    ap.add_argument("--skip-webp", dest="skip_webp", action="store_true", default=True,
                    help="Sari imaginile care sunt deja .webp (implicit ON)")
    ap.add_argument("--no-skip-webp", dest="skip_webp", action="store_false",
                    help="Procesează și sursele .webp")
    ap.add_argument("--stem-filter", action="store_true",
                    help="Păstrează o singură sursă per ‘stem’ (preferă .webp)")
    ap.add_argument("--dedup-url", action="store_true", help="Dedupează după URL fără query")

    # Moduri & confirmare:
    ap.add_argument("--dry-run", action="store_true", help="Rulează DOAR simulare (nu modifică Shopify).")
    ap.add_argument("--confirm", action="store_true",
                    help="Afișează o confirmare LIVE/DRY-RUN la pornire (util în .exe).")

    args = ap.parse_args()
    # interval valid pentru WebP
    args.quality = max(60, min(args.quality, 100))
    is_frozen = getattr(sys, 'frozen', False)

    # 🔧 FORȚĂM GUI mereu (dacă vrei asta)
    args.gui = True

    # Confirmare opțională LIVE/DRY-RUN (ca în screenshot):
    if (is_frozen or args.gui or args.confirm):
        run_live = ask_yes_no(
            "Confirmare",
            (
                "Vrei să APLICI modificările în Shopify?\n\n"
                f"Limită imagini: {args.limit}\n"
                f"Calitate WebP: {args.quality}\n\n"
                "Recomandat: fă mai întâi un test cu DRY-RUN."
            )
        )
        args.dry_run = (not run_live)
    # altfel: DRY-RUN doar dacă a fost cerut explicit prin flag (implicit LIVE)

    mode_str = "LIVE" if not args.dry_run else "DRY-RUN"
    print(f"[MODE] {mode_str} | limit={args.limit} | skip-webp={args.skip_webp} | quality={args.quality}")

    out_dir = pick_out_dir(args.out) if args.gui else Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Salvăm WEBP în: {out_dir}")
    if args.dry_run:
        print("[INFO] DRY-RUN: nu se încarcă/atașează/șterge nimic în Shopify.")

    control = start_control_ui_mainthread()
    ui_pump(control)
    if control and getattr(control, "status_var", None):
        control.status_var.set("Se pregătește lista de produse…")
    ui_pump(control)

    sess = session_with_retries()

    # === Bulk #1: images ===
    cancel_if_running()
    op1 = start_bulk_images()
    ui_pump(control)
    cur1 = poll_bulk(op1, control)
    if cur1.get("status") != "COMPLETED":
        print(f"[WARN] Bulk images ended with status: {cur1}")
        if control and getattr(control, "root", None):
            try:
                control.root.destroy()  # type: ignore
            except Exception:
                pass
        return
    lines1 = download_bulk_ndjson(cur1["url"], sess, control)

    # === Bulk #2: media + variants ===
    cancel_if_running()
    op2 = start_bulk_media_and_variants()
    ui_pump(control)
    cur2 = poll_bulk(op2, control)
    if cur2.get("status") != "COMPLETED":
        print(f"[WARN] Bulk media/variants ended with status: {cur2}")
        if control and getattr(control, "root", None):
            try:
                control.root.destroy()  # type: ignore
            except Exception:
                pass
        return
    lines2 = download_bulk_ndjson(cur2["url"], sess, control)

    lines = lines1 + lines2
    print(f"[DEBUG] Linii NDJSON totale: {len(lines)}")

    items = iter_bulk_items(lines)

    # Filtre opționale
    if args.dedup_url:
        seen = set(); tmp = []
        for it in items:
            key = (it.product_id, it.url.split("?", 1)[0])
            if key in seen:
                continue
            seen.add(key); tmp.append(it)
        items = tmp

    # Grupare pe produs
    by_product = defaultdict(list)
    for it in items:
        by_product[it.product_id].append(it)

    found_total = 0
    converted_ok = 0
    skipped_webp_cnt = 0
    dry_cnt = 0
    error_cnt = 0
    processed_images = 0

    try:
        for pid, plist in by_product.items():
            ui_pump(control)
            if control and control.stopped:
                print("[STOP] Oprit de utilizator (la nivel de produs).")
                break

            title = plist[0].product_title

            # opțional: stem-filter => un singur fișier per stem
            if args.stem_filter:
                best = {}
                for it in plist:
                    stem, ext = stem_and_ext(it.url)
                    pr = {'.webp':5, '.jpg':4, '.jpeg':4, '.png':3, '.gif':2, '.avif':1}.get(ext, 0)
                    if stem not in best or pr > best[stem][0]:
                        best[stem] = (pr, it)
                plist = [v[1] for v in best.values()]

            # Media existente (pt. identificat ce ștergem)
            # Media existente + URL-uri folosite de VARIANTE (direct din Shopify)
            if not args.dry_run:
                existing, variant_urls_for_product = list_media_and_variant_urls(pid)
            else:
                existing, variant_urls_for_product = [], set()

            url_to_mediaid = {}
            for row in existing:
                base = row["url"].split("?", 1)[0]
                url_to_mediaid[base] = row["id"]

            # selectăm doar imaginile non-WEBP (sau toate dacă --no-skip-webp)
            candidates: List[ProdImage] = []
            for it in plist:
                if args.skip_webp and is_webp_url(it.url):
                    skipped_webp_cnt += 1
                    if not args.dry_run:
                        # nu facem nimic cu cele deja webp
                        pass
                else:
                    candidates.append(it)

            if control and getattr(control, "status_var", None):
                control.status_var.set(f"Produs: {title[:40]}… | cand.: {len(candidates)}")
            ui_pump(control)

            found_total += len(candidates)
            if not candidates:
                continue

            new_media_ids: List[str] = []
            to_delete_after: List[str] = []

            for it in candidates:
                ui_pump(control)
                if control and control.stopped:
                    print("[STOP] Oprit de utilizator (în timpul imaginilor).")
                    break

                # Respectăm limita globală pe imagini
                if args.limit and processed_images >= args.limit:
                    print(f"[LIMIT] Atinsă limita globală de imagini: {args.limit}")
                    break

                processed_images += 1

                # numele original din URL (ex: geanta_neagra.png)
                orig_name = original_filename_from_url(it.url)
                stem, _ = os.path.splitext(orig_name)
                if not stem:
                    stem = "img"
                # salvăm local ca geanta_neagra.webp
                local_webp = out_dir / f"{stem}.webp"

                try:
                    if args.dry_run:
                        dry_cnt += 1
                        base_url = it.url.split("?", 1)[0]
                        # dacă imaginea este folosită de o variantă, NU o marcăm pt șters
                        if base_url not in variant_urls_for_product:
                            old_id = url_to_mediaid.get(base_url)
                            if old_id:
                                to_delete_after.append(old_id)
                        continue

                    # 1) download + convert
                    lp = download_convert_webp(it.url, local_webp, quality=args.quality, sess=sess)
                    ui_pump(control)
                    # 2) staged upload
                    res_url = staged_upload_local_file(lp, sess)
                    ui_pump(control)
                    # 3) attach media
                    mid, _purl, errs = attach_media_to_product(pid, res_url, it.alt_text)
                    ui_pump(control)
                    if errs:
                        error_cnt += 1
                        continue
                    if mid:
                        new_media_ids.append(mid)
                        converted_ok += 1
                        base_url = it.url.split("?", 1)[0]
                        # dacă imaginea NU este folosită de variante, putem marca media veche pentru șters
                        if base_url not in variant_urls_for_product:
                            old_id = url_to_mediaid.get(base_url)
                            if old_id:
                                to_delete_after.append(old_id)
                except RuntimeError:
                    error_cnt += 1
                except Exception:
                    error_cnt += 1

                time.sleep(0.30)
                ui_pump(control)

            # După adăugare, ștergem vechile media non-webp înlocuite (LIVE)
            if not args.dry_run and new_media_ids and to_delete_after and not (control and control.stopped):
                to_delete_after = sorted(set(to_delete_after))
                delete_media(pid, to_delete_after)

            if args.limit and processed_images >= args.limit:
                break

    finally:
        print("[INFO] Operare încheiată.")
        try:
            show_summary_popup(found_total, converted_ok, skipped_webp_cnt, error_cnt, dry_cnt)
        finally:
            if TK_AVAILABLE and control and getattr(control, "root", None):
                try:
                    control.root.destroy()  # type: ignore
                except Exception:
                    pass

if __name__ == "__main__":
    main()
