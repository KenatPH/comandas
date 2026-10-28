# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the app (development):**
```bash
python app.py
```

**Build the Windows executable:**
```bash
python build.py
```
This runs PyInstaller with `--onefile --windowed`, producing `dist/ComandarServer.exe`. It also copies `app_state.json`, icon files, and `comandar_server/bin/` (SumatraPDF) to `dist/`.

**Install dependencies manually:**
```bash
pip install PySide6 requests pywin32
```

There are no automated tests.

## Architecture

The entire application lives in a single file: `app.py`. It is a Windows desktop app (PySide6 GUI) that also runs a local HTTP server.

### Key subsystems

**State management** — `app_state.json` persists the PIN, printer list, comanda-to-printer assignments, and selected agency. Loaded via `load_state()` at startup; every mutation calls `save_state(state)` immediately.

**HTTP server** (`ThreadingHTTPServer` on port 8443) — Started/stopped by the "Iniciar servidor" toggle button. Handles:
- `GET /healthz` — liveness check
- `GET /status` — current printers, assignments, PIN, Sumatra path
- `GET /printers` — discovered local printers
- `GET /print?comanda_id=&id_operti=[&method=auto|sumatra][&printer=NAME]` — download PDF from `adm.premium-soft.com` and print it

**Cloudflare tunnel & Watchdog** — On server start, `cloudflared tunnel --url http://localhost:8443` is launched as a subprocess. The generated `*.trycloudflare.com` URL is extracted from stdout and POSTed to `http://adm.premium-soft.com/Configuracion/GuardarUrlCloudflare` along with the PIN and agency ID. Stdout is drained continuously in background to prevent pipe buffer hangs. A dedicated watchdog (`_cloudflared_watchdog`) validates every 60 seconds that the tunnel process is running and reachable via `GET /healthz`. If the tunnel expires or drops, it automatically terminates the old process, launches a new tunnel, extracts the new URL, and posts it to the server.

**PDF printing pipeline** — `print_pdf_from_url_auto(url, printer_name)`:
1. Downloads the PDF to a temp file via `requests`.
2. Tries SumatraPDF (`bin/SumatraPDF-3.5.2-64.exe`) with `-print-to <printer>`.
3. Falls back to `win32api.ShellExecute("print")` / `os.startfile("print")`.
4. Cleans up the temp file after a short delay.

**Printer discovery** — Uses `win32print.EnumPrinters(PRINTER_ENUM_LOCAL)` (pywin32) or PowerShell `Get-Printer` as fallback (strictly local printers). Printers containing UNC paths, IP addresses, or suspicious patterns are filtered out by `_is_safe_printer()`. Preserves all installed local printers even if they share the same name.

**Remote API calls** — `ComandasFetcher` and `AgenciasFetcher` (QObject workers) hit:
- `https://adm.premium-soft.com/Comandas/ConsultarLote?pin=<PIN>`
- `https://adm.premium-soft.com/Agencias/ConsultarLote?pin=<PIN>`

These run in daemon threads and emit Qt signals back to the UI.

### External dependencies

| Tool | Purpose | Location |
|------|---------|---------|
| SumatraPDF | Silent PDF printing | `comandar_server/bin/SumatraPDF-3.5.2-64.exe` |
| cloudflared | Expose local server via Cloudflare tunnel | Must be on `PATH` |
| pywin32 | Printer discovery + ShellExecute fallback | Optional; PowerShell fallback exists |

SumatraPDF is bundled under `comandar_server/bin/` and included in the PyInstaller build via `--add-data "comandar_server/bin;bin"`. At runtime it is found via `_get_sumatra_paths()`, which checks both `sys._MEIPASS` (onefile .exe) and the script directory.

### Logging

Logging is disabled by default (all `_log_debug` / `_log_exception` calls are no-ops). To enable, uncomment the file-write blocks in those functions; logs go to `print_debug.log` in `os.getcwd()`.