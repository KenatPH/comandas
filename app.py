# main.py
import os
import sys

# Forzar el plugin de plataforma X11 (xcb) en Linux para evitar crash/segfault por Wayland en PySide6
if sys.platform != "win32" and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "xcb"

import json
import threading
import tempfile
import subprocess
import shutil
from typing import Optional, List, Tuple
import time
import requests
import re

from urllib.parse import urlencode
from urllib.parse import urlparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QMessageBox, QLabel, QDialog,
    QHeaderView, QLineEdit, QFormLayout, QInputDialog, QComboBox,
    QSystemTrayIcon, QMenu, QProgressBar, QCheckBox, QPlainTextEdit,
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QFont, QColor

try:
    import win32api
    import win32print
    import win32con
except Exception:
    win32api = None
    win32print = None
    win32con = None

# ==============================
# Configuración de la aplicación
# ==============================
APP_VERSION = "2.0.6"
APP_STATE_FILE = "app_state.json"
DEBUG_LOG_FILE = os.path.join(os.getcwd(), "print_debug.log")

# Configuración del monitor y ciclo de vida de Cloudflare
CLOUDFLARE_HEALTH_CHECK_INTERVAL = 60  # Intervalo en segundos para comprobar la salud del túnel
CLOUDFLARE_HEALTH_CHECK_TIMEOUT = 10   # Tiempo de espera en segundos para la respuesta de /healthz

SERVERS = {
    "local":     {"label": "Local",                 "base_url": "http://adminbiz.local"},
    "adm":       {"label": "ADM (Varios países)",   "base_url": "https://adm.premium-soft.com"},
    "coladm":    {"label": "COLADM (Colombia)",     "base_url": "https://coladm.premium-soft.com/ADMCOL"},
    "coladm_qa": {"label": "COLADM QA (Colombia)",  "base_url": "https://coladm.premium-soft.com/QA_admincol"},
    "colvip":    {"label": "COLVIP (Colombia VIP)", "base_url": "https://colvip.premium-soft.com/ADMCOLGRAlVIP"},
}


def _log_debug(msg: str) -> None:
    # try:
    #     with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
    #         f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    # except Exception:
    #     pass
    pass


def _log_exception(context: str, err: BaseException) -> None:
    # try:
    #     with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
    #         f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + context + "\n")
    #         traceback.print_exc(file=f)
    # except Exception:
    #     pass
    pass


def _read_log_tail(max_lines: int = 80) -> str:
    # try:
    #     if not os.path.isfile(DEBUG_LOG_FILE):
    #         return "(sin log)"
    #     with open(DEBUG_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
    #         lines = f.readlines()
    #     return "".join(lines[-max_lines:])
    # except Exception:
    #     return "(no se pudo leer el log)"
    return "(logging deshabilitado)"



DEFAULT_STATE = {
    "pin": "41038",
    "printers": [],           # lista de nombres de impresoras
    "assignments": {},        # { "Nombre Impresora": {comanda_dict} }
    "assignments_by_comanda": {},  # { id_comanda: "Nombre Impresora" }
    "last_id_operti": "",     # recuerda el último id_operti usado para imprimir
    "agencia": None,          # { "id_agencia": "...", "nombre": "..." } - agencia seleccionada
    "api_server": None,        # clave del servidor API: "adm", "coladm", "coladm_qa", "colvip"
    "show_virtual_printer": False, # booleano para habilitar/ver impresora virtual
    "server": {                # configuración del servidor local
        "host": "0.0.0.0",
        "port": 8443
    }
}

def load_state() -> dict:
    if not os.path.exists(APP_STATE_FILE):
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        with open(APP_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        state = json.loads(json.dumps(DEFAULT_STATE))
        if isinstance(data, dict):
            state.update(data)
        if not isinstance(state.get("printers", []), list):
            state["printers"] = []
        if not isinstance(state.get("assignments", {}), dict):
            state["assignments"] = {}
        if not isinstance(state.get("last_id_operti", ""), str):
            state["last_id_operti"] = ""
        if not isinstance(state.get("assignments_by_comanda", {}), dict):
            state["assignments_by_comanda"] = {}
        if "agencia" not in state:
            state["agencia"] = None
        if state.get("api_server") not in SERVERS:
            state["api_server"] = None
        if "show_virtual_printer" not in state:
            state["show_virtual_printer"] = False
        if not isinstance(state.get("server", {}), dict):
            state["server"] = json.loads(json.dumps(DEFAULT_STATE["server"]))
        
        # Limpiar impresoras problemáticas del estado guardado
        state = _clean_unsafe_printers_from_state(state)
        
        return state
    except Exception:
        return json.loads(json.dumps(DEFAULT_STATE))


def _clean_unsafe_printers_from_state(state: dict) -> dict:
    """
    Limpia impresoras inseguras del estado guardado.
    """
    if not isinstance(state.get("printers", []), list):
        return state
    
    original_printers = state["printers"].copy()
    safe_printers = []
    removed_printers = []
    
    for printer in original_printers:
        if _is_safe_printer(printer):
            safe_printers.append(printer)
        else:
            removed_printers.append(printer)
            _log_debug(f"Auto-limpieza: Impresora insegura '{printer}' removida del estado guardado")
    
    if removed_printers:
        state["printers"] = safe_printers
        
        # También limpiar asignaciones de impresoras removidas
        if isinstance(state.get("assignments", {}), dict):
            for removed_printer in removed_printers:
                if removed_printer in state["assignments"]:
                    del state["assignments"][removed_printer]
                    _log_debug(f"Auto-limpieza: Asignación de impresora insegura '{removed_printer}' removida")
        
        # Limpiar también assignments_by_comanda que apunten a impresoras removidas
        if isinstance(state.get("assignments_by_comanda", {}), dict):
            safe_assignments = {}
            for comanda_id, printer_name in state["assignments_by_comanda"].items():
                if printer_name not in removed_printers:
                    safe_assignments[comanda_id] = printer_name
                else:
                    _log_debug(f"Auto-limpieza: Asignación de comanda {comanda_id} a impresora insegura '{printer_name}' removida")
            state["assignments_by_comanda"] = safe_assignments
        
        # Guardar el estado limpiado
        save_state(state)
        _log_debug(f"Auto-limpieza completada: {len(removed_printers)} impresoras inseguras removidas")
    
    return state

def save_state(state: dict):
    try:
        with open(APP_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("No se pudo guardar el estado:", e)


def get_base_url(state: dict) -> str:
    """Devuelve la URL base del servidor API según la configuración."""
    key = state.get("api_server") or "adm"
    return SERVERS.get(key, SERVERS["adm"])["base_url"]


# ==============================
# Descubrir impresoras (Windows / Linux)
def _discover_printers_powershell() -> List[str]:
    """Usa PowerShell para listar impresoras si no hay pywin32 (solo impresoras locales)."""
    try:
        # PowerShell command para obtener solo impresoras locales
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Printer | Where-Object {$_.Type -eq 'Local' -or $_.Type -eq 'Unknown'} | Select-Object -ExpandProperty Name"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        _log_debug(f"PowerShell Get-Printer (local) rc={res.returncode} stdout_len={len(res.stdout)} stderr_len={len(res.stderr)}")
        if res.returncode == 0 and res.stdout:
            names = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return names
    except Exception:
        pass
    return []


def _discover_printers_linux() -> List[str]:
    """Usa lpstat en Linux para listar impresoras locales."""
    try:
        # Intentar con lpstat -e primero (devuelve solo nombres de impresoras listas)
        res = subprocess.run(["lpstat", "-e"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout:
            names = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return names
        
        # Fallback a lpstat -p
        res = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout:
            names = []
            for line in res.stdout.splitlines():
                if line.startswith("printer "):
                    parts = line.split()
                    if len(parts) > 1:
                        names.append(parts[1])
            return names
    except Exception:
        pass
    return []


def discover_printers() -> List[str]:
    """
    Intenta obtener la lista de impresoras locales.
    En Windows: pywin32 -> PowerShell (solo impresoras locales).
    En Linux: lpstat.
    Filtra impresoras remotas problemáticas.
    Preserva impresoras con el mismo nombre si existen múltiples en el sistema.
    """
    printers = []
    if sys.platform == "win32":
        try:
            if win32print:
                # Solo impresoras locales, no conexiones remotas
                flags = win32print.PRINTER_ENUM_LOCAL
                printers = [p[2] for p in win32print.EnumPrinters(flags)]
        except Exception:
            pass

        if not printers:
            printers = _discover_printers_powershell()
    else:
        printers = _discover_printers_linux()

    out = []
    
    # Agregar la impresora virtual/simulador para pruebas/desarrollo local si está habilitado
    if load_state().get("show_virtual_printer", False):
        virtual_name = "Impresora Virtual (Simulador)"
        out.append(virtual_name)
    
    for name in printers:
        if name:
            if _is_safe_printer(name):
                out.append(name)
            else:
                _log_debug(f"Impresora filtrada (remota/insegura): {name}")
    return out


def _is_safe_printer(printer_name: str) -> bool:
    """
    Verifica si una impresora es segura para usar (local, no remota problemática).
    """
    if not printer_name:
        return False
    
    name_lower = printer_name.lower()
    
    # Filtrar impresoras remotas por IP o dominios sospechosos
    suspicious_patterns = [
        '\\',  # Impresoras de red UNC
        '//',  # Impresoras de red con doble slash
        'tcp/',  # Impresoras TCP/IP
        'ip_',   # Impresoras IP
        'http://',  # Impresoras HTTP
        'https://',  # Impresoras HTTPS
        '.cn',      # Dominios chinos
        '.hk',      # Hong Kong
        '.tw',      # Taiwan
        'china',    # Palabra china
        'chinese',  # Palabra china en inglés
        'remote',   # Impresoras remotas
        'network',  # Impresoras de red genéricas
    ]
    
    for pattern in suspicious_patterns:
        if pattern in name_lower:
            _log_debug(f"Impresora '{printer_name}' marcada como insegura por patrón: {pattern}")
            return False
    
    # Filtrar impresoras que contengan IPs
    import re
    ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    if re.search(ip_pattern, printer_name):
        _log_debug(f"Impresora '{printer_name}' marcada como insegura por contener IP")
        return False
    
    # Permitir solo impresoras locales comunes
    safe_patterns = [
        'usb',      # Impresoras USB
        'local',    # Impresoras locales
        'default',  # Impresora por defecto
        'hp',       # HP
        'canon',    # Canon
        'epson',    # Epson
        'brother',  # Brother
        'lexmark',  # Lexmark
        'samsung',  # Samsung
        'xerox',    # Xerox
        'ricoh',    # Ricoh
        'kyocera',  # Kyocera
        'okidata',  # Okidata
        'xprinter', # XPrinter
        'thermal',  # Impresoras térmicas
        'receipt',  # Impresoras de recibos
        'pos',      # Punto de venta
    ]
    
    # Si contiene un patrón seguro, permitir
    for pattern in safe_patterns:
        if pattern in name_lower:
            return True
    
    # Si no tiene patrón seguro pero tampoco sospechoso, permitir con advertencia
    _log_debug(f"Impresora '{printer_name}' permitida pero sin patrón seguro reconocido")
    return True


# ==============================
# Construcción de URL del ticket
# ==============================
def build_ticket_url(pin: str, comanda_id: Optional[str], id_operti: str, base_url: str = "https://adm.premium-soft.com") -> str:
    """
    Construye la URL del servicio PDF (tiquete) usando PIN, comanda y id_operti.
    """
    base = f"{base_url}/Reportes_ventas/ComprobanteDocVenta"
    comanda_param = f"comanda_{comanda_id}" if comanda_id else ""
    params = {
        "pin": pin,
        "id_operti": id_operti,
        "id_fact_elect": "",
        "tipo_reporte": "pdf",
        "formato_doc": "tiquete",
        "orientacion_doc": "vertical",
        "id_tipo_moneda": "",
        "multiplicar_dividir": "",
        "factor_cambio": "0,00",
        "ver_hijos": "0",
        "marcar_copia": "0",
        "comanda": comanda_param,
        "precuenta": "",
        "es_para_llevar": "0",
        "idioma": "esp",
    }
    return f"{base}?{urlencode(params, doseq=True)}"


# ==============================
# Descarga e impresión de PDF
# ==============================
try:
    import win32api  # type: ignore  # opcional
except Exception:
    win32api = None

def _get_sumatra_paths():
    """Obtiene las rutas de SumatraPDF, adaptándose al entorno de ejecución."""
    paths = []
    
    # Obtener directorio base
    if getattr(sys, 'frozen', False):
        # Ejecutándose como .exe
        # Con --onefile, los archivos incluidos con --add-data están en sys._MEIPASS
        # Con --onedir, están en el directorio del ejecutable
        base_dir = os.path.dirname(sys.executable)
        
        # Si existe sys._MEIPASS (modo --onefile), también buscar ahí
        if hasattr(sys, '_MEIPASS'):
            meipass_dir = sys._MEIPASS
            _log_debug(f"Ejecutándose como .exe (onefile) desde: {base_dir}, MEIPASS: {meipass_dir}")
            # Agregar rutas en MEIPASS primero (prioridad)
            paths.extend([
                os.path.join(meipass_dir, "bin", "SumatraPDF.exe"),
                os.path.join(meipass_dir, "bin", "SumatraPDF-3.5.2-64.exe"),
                os.path.join(meipass_dir, "SumatraPDF.exe"),
                os.path.join(meipass_dir, "SumatraPDF-3.5.2-64.exe"),
            ])
        else:
            _log_debug(f"Ejecutándose como .exe (onedir) desde: {base_dir}")
        
        # También buscar en el directorio del ejecutable (para --onedir o si están copiados manualmente)
        paths.extend([
            os.path.join(base_dir, "bin", "SumatraPDF.exe"),
            os.path.join(base_dir, "bin", "SumatraPDF-3.5.2-64.exe"),
            os.path.join(base_dir, "SumatraPDF.exe"),
            os.path.join(base_dir, "SumatraPDF-3.5.2-64.exe"),
        ])
    else:
        # Ejecutándose como script
        base_dir = os.path.dirname(os.path.abspath(__file__))
        _log_debug(f"Ejecutándose como script desde: {base_dir}")
        
        # Rutas para script
        paths.extend([
            os.path.join(base_dir, "bin", "SumatraPDF.exe"),
            os.path.join(base_dir, "bin", "SumatraPDF-3.5.2-64.exe"),
            os.path.join(base_dir, "comandar_server", "bin", "SumatraPDF.exe"),
            os.path.join(base_dir, "comandar_server", "bin", "SumatraPDF-3.5.2-64.exe"),
            os.path.join(os.getcwd(), "bin", "SumatraPDF.exe"),
            os.path.join(os.getcwd(), "bin", "SumatraPDF-3.5.2-64.exe"),
        ])
    
    return paths

SUMATRA_PATHS = _get_sumatra_paths()




    # Nota: la definición de download_pdf_to_temp aparece más abajo.


def _list_printers() -> list[str]:
    """Mantengo esta función para compatibilidad interna (pywin32 si existe)."""
    if not win32print:
        return _discover_printers_powershell()
    flags = win32print.PRINTER_ENUM_LOCAL
    return [p[2] for p in win32print.EnumPrinters(flags)]


def _set_default_printer_temporarily(target: str) -> str:
    """Devuelve el nombre de la impresora predeterminada anterior y deja 'target' como nueva por un momento."""
    if not win32print:
        raise RuntimeError("pywin32 no disponible. Instala 'pywin32'.")
    current = win32print.GetDefaultPrinter()
    win32print.SetDefaultPrinter(target)
    return current


def _restore_default_printer(previous: str):
    if win32print and previous:
        try:
            win32print.SetDefaultPrinter(previous)
        except Exception:
            pass


def print_pdf_via_windows_default(pdf_path: str, target_printer: str | None = None, settle_seconds: float = 2.0):
    """
    Imprime usando ShellExecute('print') a la impresora predeterminada.
    Si 'target_printer' viene, cambiamos temporalmente la predeterminada a esa impresora.
    """
    if not win32api or not win32print:
        raise RuntimeError("pywin32 no disponible. Instala 'pywin32' para imprimir sin Sumatra.")

    previous_default = None
    try:
        if target_printer:
            printers = _list_printers()
            if target_printer not in printers:
                raise RuntimeError(f"La impresora '{target_printer}' no está instalada.")
            previous_default = _set_default_printer_temporarily(target_printer)

        dest = target_printer if target_printer else "Impresora predeterminada"
        print(f"🖨️ Enviando archivo a imprimir usando ShellExecute (Windows)...")
        print(f"   Destino: {dest}")

        # Método 1: ShellExecute con "print" (más directo)
        try:
            _log_debug("Intentando ShellExecute 'print'...")
            win32api.ShellExecute(0, "print", pdf_path, None, ".", 0)
            _log_debug("ShellExecute 'print' exitoso")
            print(f"✅ ShellExecute 'print' exitoso.")
            time.sleep(settle_seconds)
            return
        except Exception as e:
            _log_debug(f"ShellExecute 'print' fallo: {e}")
            print(f"⚠️ ShellExecute 'print' falló: {e}. Intentando fallback...")
        
        # Método 2: os.startfile como fallback
        try:
            _log_debug("Intentando os.startfile 'print'...")
            os.startfile(pdf_path, "print")
            _log_debug("os.startfile 'print' exitoso")
            print(f"✅ os.startfile 'print' exitoso.")
            time.sleep(settle_seconds)
            return
        except Exception as e:
            _log_debug(f"os.startfile 'print' fallo: {e}")
            print(f"⚠️ os.startfile 'print' falló: {e}. Intentando fallback...")
        
        # Método 3: Abrir PDF para impresión manual
        try:
            _log_debug("Abriendo PDF para impresión manual...")
            win32api.ShellExecute(0, "open", pdf_path, None, ".", 1)
            _log_debug("PDF abierto para impresión manual")
            print(f"ℹ️ PDF abierto en pantalla para impresión manual.")
            return
        except Exception as e:
            _log_debug(f"ShellExecute 'open' fallo: {e}")
            print(f"❌ Error al abrir PDF para impresión manual: {e}")
            raise RuntimeError(f"No se pudo imprimir el PDF. Error: {e}")

    finally:
        if previous_default:
            _restore_default_printer(previous_default)


def print_pdf_from_url_default_only(url: str, printer_name: str | None):
    """Descarga el PDF y lo manda a imprimir con el método de Windows (sin Sumatra)."""
    pdf_path = download_pdf_to_temp(url)
    try:
        print_pdf_via_windows_default(pdf_path, target_printer=printer_name)
    finally:
        try:
            os.remove(pdf_path)
        except Exception:
            pass


def _find_sumatra() -> Optional[str]:
    env = os.environ.get("SUMATRA_EXE") or os.environ.get("SUMATRA_PATH")
    if env and os.path.isfile(env):
        _log_debug(f"Sumatra por ENV: {env}")
        return env
    
    _log_debug(f"Buscando Sumatra en {len(SUMATRA_PATHS)} rutas:")
    for p in SUMATRA_PATHS:
        exists = os.path.isfile(p)
        _log_debug(f"  {p} -> exists={exists}")
        if exists:
            _log_debug(f"Sumatra encontrado: {p}")
            return p
    
    which_result = shutil.which("SumatraPDF.exe")
    _log_debug(f"shutil.which('SumatraPDF.exe') = {which_result}")
    if which_result:
        _log_debug(f"Sumatra por PATH: {which_result}")
        return which_result
    
    _log_debug("Sumatra no encontrado en ninguna ruta")
    return None

def _sumatra_debug_info() -> str:
    """Devuelve un texto con el estado de detección de Sumatra y rutas probadas."""
    lines: List[str] = []
    env1 = os.environ.get("SUMATRA_EXE")
    env2 = os.environ.get("SUMATRA_PATH")
    if env1:
        lines.append(f"SUMATRA_EXE={env1} (exists={os.path.isfile(env1)})")
    if env2:
        lines.append(f"SUMATRA_PATH={env2} (exists={os.path.isfile(env2)})")
    for p in SUMATRA_PATHS:
        lines.append(f"check: {p} (exists={os.path.isfile(p)})")
    which = shutil.which("SumatraPDF.exe")
    if which:
        lines.append(f"which: {which}")
    return "\n".join(lines)

def download_pdf_to_temp(url: str) -> str:
    print(f"📥 Descargando PDF...")
    print(f"   URL: {url}")
    _log_debug(f"Descargando PDF: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, timeout=30, headers=headers)
    _log_debug(f"HTTP status={r.status_code} content-type={r.headers.get('content-type','')} len={len(r.content)}")
    r.raise_for_status()

    content_type = (r.headers.get("content-type") or "").lower()
    is_pdf = ("application/pdf" in content_type) or (r.content[:4] == b"%PDF")

    if not is_pdf:
        # Guardar el cuerpo para inspección
        fd_tmp, nonpdf_path = tempfile.mkstemp(suffix=".html")
        os.close(fd_tmp)
        try:
            with open(nonpdf_path, "wb") as f:
                f.write(r.content)
        except Exception:
            pass
        head = r.content[:200].decode("latin1", errors="replace")
        _log_debug(f"Respuesta no es PDF. Guardado en: {nonpdf_path}. Primeros bytes: {head!r}")
        print(f"❌ Respuesta recibida no es un PDF. Guardado para inspección en: {nonpdf_path}")
        raise RuntimeError(
            f"La URL no devolvió un PDF (content-type={content_type!r}, bytes={len(r.content)}). Archivo guardado: {nonpdf_path}"
        )

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(r.content)
    _log_debug(f"PDF guardado en: {path} bytes={len(r.content)}")
    print(f"   Descarga exitosa. PDF guardado temporalmente en: {path} ({len(r.content)} bytes)")
    return path


def print_pdf_with_sumatra(pdf_path: str, printer_name: Optional[str]) -> None:
    exe = _find_sumatra()
    if not exe:
        raise RuntimeError("SumatraPDF.exe no encontrado. Colócalo en bin/SumatraPDF.exe o en el PATH.")
    
    # Verificar que el archivo PDF existe y no está vacío
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"Archivo PDF no encontrado: {pdf_path}")
    
    file_size = os.path.getsize(pdf_path)
    if file_size == 0:
        raise RuntimeError(f"Archivo PDF está vacío: {pdf_path}")
    
    _log_debug(f"Archivo PDF verificado: {pdf_path} ({file_size} bytes)")
    
    cmd = [exe]
    if printer_name:
        cmd += ["-print-to", printer_name]
    else:
        cmd += ["-print-to-default"]
    # Asegurar 32/64-bits correcto por defecto; usar -nostartup para evitar delays
    cmd += ["-silent", "-exit-when-done", pdf_path]
    
    dest = printer_name if printer_name else "Impresora predeterminada"
    print(f"🖨️ Enviando a SumatraPDF...")
    print(f"   Destino: {dest}")
    print(f"   Comando: {' '.join(cmd)}")
    _log_debug(f"Ejecutando Sumatra: {' '.join(cmd)}")
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        _log_debug(f"Sumatra rc={res.returncode} stdout={res.stdout[:500]} stderr={res.stderr[:500]}")
        if res.returncode != 0:
            raise RuntimeError(f"SumatraPDF fallo rc={res.returncode} stderr={res.stderr}")
        print(f"✅ Impresión enviada a SumatraPDF exitosamente.")
    except subprocess.TimeoutExpired:
        _log_debug("SumatraPDF timeout - intentando método alternativo")
        print(f"⚠️ SumatraPDF timeout. Intentando método alternativo...")
        # Método alternativo: abrir y imprimir
        try:
            subprocess.run([exe, "-print", pdf_path], timeout=10)
            print(f"✅ Impresión enviada con comando alternativo de SumatraPDF.")
        except Exception as e:
            print(f"❌ Error al imprimir con SumatraPDF: {e}")
            raise RuntimeError(f"SumatraPDF fallo con timeout: {e}")


def print_pdf_default(pdf_path: str) -> None:
    if not win32api:
        raise RuntimeError("win32api no disponible. Instala pywin32 o usa Sumatra.")
    # Envía al default printer
    _log_debug("ShellExecute print (default)")
    print(f"🖨️ Enviando archivo a la impresora predeterminada usando ShellExecute...")
    win32api.ShellExecute(0, "print", pdf_path, None, ".", 0)
    print(f"✅ ShellExecute 'print' (default) ejecutado.")


def print_pdf_linux(pdf_path: str, printer_name: Optional[str]) -> None:
    """Imprime un PDF en Linux usando el comando lp de CUPS."""
    if not os.path.exists(pdf_path):
        raise RuntimeError(f"Archivo PDF no encontrado: {pdf_path}")
    
    cmd = ["lp"]
    if printer_name:
        cmd += ["-d", printer_name]
    cmd.append(pdf_path)
    
    dest = printer_name if printer_name else "Impresora predeterminada"
    print(f"🖨️ Enviando al sistema de impresión de Linux (CUPS)...")
    print(f"   Destino: {dest}")
    print(f"   Comando: {' '.join(cmd)}")
    _log_debug(f"Ejecutando impresión en Linux: {' '.join(cmd)}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        _log_debug(f"lp exit code={res.returncode} stdout={res.stdout} stderr={res.stderr}")
        if res.returncode != 0:
            raise RuntimeError(f"lp falló con código {res.returncode}: {res.stderr}")
        print(f"✅ Impresión enviada a CUPS exitosamente. Respuesta: {res.stdout.strip()}")
    except Exception as e:
        _log_exception("Error imprimiendo en Linux", e)
        print(f"❌ Error al imprimir en Linux: {e}")
        raise RuntimeError(f"No se pudo imprimir en Linux. Error: {e}")


def print_pdf_from_url_auto(url: str, printer_name: Optional[str]) -> None:
    """
    Descarga el PDF y lo imprime:
    En Linux:
       Usa el comando lp de CUPS.
    En Windows:
       1) Intenta con SumatraPDF (si existe), directo a `printer_name` o default.
       2) Si no hay Sumatra o falla, usa el método de Windows (ShellExecute 'print'),
          cambiando temporalmente la impresora predeterminada si corresponde.
    """
    pdf_path = download_pdf_to_temp(url)
    try:
        # Comportamiento del Simulador de Impresora Virtual
        if printer_name == "Impresora Virtual (Simulador)":
            try:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                dest_dir = os.path.join(base_dir, "comandas_impresas")
                os.makedirs(dest_dir, exist_ok=True)
                
                # Intentar parsear comanda_id e id_operti de la URL
                import urllib.parse
                parsed_url = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed_url.query)
                comanda_id = params.get("comanda_id", ["unknown"])[0]
                id_operti = params.get("id_operti", ["unknown"])[0]
                
                filename = f"comanda_simulada_cmd{comanda_id}_op{id_operti}_{int(time.time())}.pdf"
                dest_path = os.path.join(dest_dir, filename)
                
                import shutil
                shutil.copy(pdf_path, dest_path)
                
                msg = f"[SIMULADOR] Impresión simulada correctamente. PDF guardado en: {dest_path}"
                print(f"✅ {msg}")
                _log_debug(msg)
                return
            except Exception as sim_err:
                _log_exception("Error en el simulador de impresora", sim_err)
                raise RuntimeError(f"Fallo en simulador de impresora: {sim_err}")

        if sys.platform != "win32":
            # Ejecución en Linux
            print_pdf_linux(pdf_path, printer_name)
            time.sleep(2)
            return

        # Ejecución en Windows
        exe = _find_sumatra()
        if exe:
            try:
                # Fuerza el camino de Sumatra si está disponible
                print_pdf_with_sumatra(pdf_path, printer_name)
                # Esperar un poco para que Sumatra procese el archivo
                time.sleep(2)
                return
            except Exception as e:
                _log_debug(f"Sumatra falló: {e}, intentando método Windows")
                # Fallback a método Windows
                pass

        # Método Windows (puede requerir pywin32)
        if printer_name:
            # Validar que la impresora exista (evitar error 31 por nombre inexistente)
            installed = _list_printers()
            _log_debug(f"Impresoras instaladas: {installed}")
            if printer_name not in installed:
                raise RuntimeError(
                    f"La impresora '{printer_name}' no está instalada. Disponibles: {installed}"
                )
            try:
                print_pdf_via_windows_default(pdf_path, target_printer=printer_name)
                # Esperar un poco para que Windows procese el archivo
                time.sleep(2)
            except Exception as e:
                _log_exception("ShellExecute/Windows print fallo", e)
                # Como último intento: usar Sumatra apuntando a default si aún existe
                exe2 = _find_sumatra()
                if exe2:
                    _log_debug("Intento final: Sumatra a default printer")
                    print_pdf_with_sumatra(pdf_path, None)
                    time.sleep(2)
                    return
                raise
        else:
            print_pdf_default(pdf_path)
            time.sleep(2)
    finally:
        # Esperar un poco más antes de eliminar el archivo
        time.sleep(3)
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
                _log_debug(f"Eliminado temporal: {pdf_path}")
        except Exception as e:
            _log_debug(f"No se pudo eliminar temporal: {pdf_path} - {e}")


def print_pdf_from_url_sumatra_only(url: str, printer_name: Optional[str]) -> None:
    """Descarga e imprime forzando Sumatra (sin fallback a ShellExecute)."""
    pdf_path = download_pdf_to_temp(url)
    try:
        print_pdf_with_sumatra(pdf_path, printer_name)
    finally:
        try:
            os.remove(pdf_path)
            _log_debug(f"Eliminado temporal: {pdf_path}")
        except Exception:
            pass


# ==============================
# Verificación e instalación de cloudflared
# ==============================
def _find_cloudflared_bin() -> Optional[str]:
    """Busca el ejecutable de cloudflared en el sistema y rutas locales."""
    # 1. Buscar en el PATH del sistema
    shutil_path = shutil.which("cloudflared")
    if shutil_path:
        return shutil_path
    
    # 2. Buscar en el directorio base del script y subdirectorios
    if getattr(sys, 'frozen', False):
        # Si se ejecuta compilado (por PyInstaller)
        base_dir = os.path.dirname(sys.executable)
        if hasattr(sys, '_MEIPASS'):
            meipass_dir = sys._MEIPASS
            local_paths = [
                os.path.join(meipass_dir, "cloudflared"),
                os.path.join(meipass_dir, "cloudflared.exe"),
                os.path.join(meipass_dir, "bin", "cloudflared"),
                os.path.join(meipass_dir, "bin", "cloudflared.exe"),
            ]
            for p in local_paths:
                if os.path.isfile(p):
                    return p
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
    ext = ".exe" if sys.platform == "win32" else ""
    local_paths = [
        os.path.join(base_dir, f"cloudflared{ext}"),
        os.path.join(base_dir, "bin", f"cloudflared{ext}"),
        os.path.join(base_dir, "comandar_server", "bin", f"cloudflared{ext}"),
        os.path.join(os.getcwd(), f"cloudflared{ext}"),
        os.path.join(os.getcwd(), "bin", f"cloudflared{ext}"),
    ]
    
    for p in local_paths:
        if os.path.isfile(p):
            # En Linux/Unix asegurar que sea ejecutable
            if sys.platform != "win32" and not os.access(p, os.X_OK):
                try:
                    os.chmod(p, 0o755)
                except Exception:
                    pass
            return p
            
    return None


def _is_cloudflared_installed() -> bool:
    """Verifica si cloudflared está instalado ejecutando cloudflared --version."""
    bin_path = _find_cloudflared_bin()
    if not bin_path:
        return False
    try:
        # Intentar ejecutar cloudflared --version directamente
        res = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        return res.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:
        return False


def _install_cloudflared() -> Tuple[bool, str]:
    """
    Instala cloudflared.
    En Windows usa winget.
    En Linux descarga el ejecutable directamente de las descargas de GitHub de cloudflared.
    Retorna (éxito, mensaje).
    """
    if sys.platform == "win32":
        try:
            # Verificar que winget esté disponible
            winget_check = subprocess.run(
                ["winget", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if winget_check.returncode != 0:
                return False, "winget no está disponible. Por favor, instala Windows Package Manager."
            
            # Ejecutar la instalación
            cmd = ["winget", "install", "--id", "Cloudflare.cloudflared", "--accept-package-agreements", "--accept-source-agreements"]
            
            # Configurar flags para ocultar la ventana de CMD en Windows
            creation_flags = subprocess.CREATE_NO_WINDOW
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creation_flags
            )
            
            # Esperar a que termine (puede tardar varios minutos)
            stdout, stderr = process.communicate(timeout=300)  # 5 minutos máximo
            
            if process.returncode == 0:
                # Esperar un poco para que el PATH se actualice
                time.sleep(2)
                # Verificar que ahora esté instalado
                if _is_cloudflared_installed():
                    return True, "cloudflared instalado correctamente."
                else:
                    return False, "Instalación completada, pero cloudflared no se encuentra en el PATH. Puede que necesites reiniciar la aplicación."
            else:
                error_msg = stderr if stderr else stdout
                return False, f"Error al instalar cloudflared: {error_msg}"
                
        except subprocess.TimeoutExpired:
            process.kill()
            return False, "La instalación tardó demasiado tiempo y fue cancelada."
        except FileNotFoundError:
            return False, "winget no se encuentra en el sistema. Por favor, instala Windows Package Manager."
        except Exception as e:
            return False, f"Error inesperado durante la instalación: {str(e)}"
    else:
        # Plataforma Linux / Unix
        try:
            import platform
            arch = platform.machine().lower()
            if "x86_64" in arch or "amd64" in arch:
                binary_name = "cloudflared-linux-amd64"
            elif "aarch64" in arch or "arm64" in arch:
                binary_name = "cloudflared-linux-arm64"
            elif "arm" in arch:
                binary_name = "cloudflared-linux-arm"
            elif "386" in arch or "686" in arch:
                binary_name = "cloudflared-linux-386"
            else:
                binary_name = "cloudflared-linux-amd64"  # fallback
            
            url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{binary_name}"
            
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            
            dest_path = os.path.join(base_dir, "cloudflared")
            
            _log_debug(f"Descargando cloudflared desde: {url} a {dest_path}")
            
            r = requests.get(url, stream=True, timeout=60)
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            # Hacerlo ejecutable
            os.chmod(dest_path, 0o755)
            
            # Verificar
            if _is_cloudflared_installed():
                return True, "cloudflared descargado e instalado correctamente."
            else:
                return False, "Se descargó cloudflared pero falló la prueba de ejecución."
                
        except Exception as e:
            _log_exception("Error descargando cloudflared en Linux", e)
            return False, f"Error al descargar cloudflared: {str(e)}"


# ==============================
# Worker: Instalar cloudflared (hilo)
# ==============================
class CloudflaredInstaller(QObject):
    finished = Signal(bool, str)  # emite (éxito, mensaje)
    status_update = Signal(str)  # emite mensaje de estado
    
    def install(self):
        """Ejecuta la instalación de cloudflared en un hilo."""
        self.status_update.emit("Descargando cloudflared...")
        success, message = _install_cloudflared()
        self.finished.emit(success, message)


# ==============================
# Diálogo: cloudflared no encontrado — opciones
# ==============================
class CloudflaredOptionsDialog(QDialog):
    ACTION_AUTO_INSTALL = "auto"
    ACTION_MANUAL       = "manual"
    ACTION_EXIT         = "exit"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("cloudflared no encontrado")
        self.setModal(True)
        self.resize(500, 240)
        self.action = self.ACTION_EXIT

        layout = QVBoxLayout(self)

        lbl = QLabel(
            "<b>cloudflared no está instalado en esta computadora.</b><br><br>"
            "cloudflared es necesario para exponer el servidor de impresión "
            "a través de internet. ¿Qué deseas hacer?"
        )
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        layout.addWidget(lbl)
        layout.addSpacing(8)

        if sys.platform == "win32":
            btn_auto = QPushButton("🔄  Instalar automáticamente en segundo plano  (requiere winget)")
            btn_auto.clicked.connect(self._auto_install)
            layout.addWidget(btn_auto)

            btn_ps = QPushButton("💻  Instalar abriendo PowerShell  (recomendado si el automático falla)")
            btn_ps.clicked.connect(self._open_powershell_install)
            layout.addWidget(btn_ps)

            btn_manual = QPushButton("📥  Ver instrucciones de instalación manual")
            btn_manual.clicked.connect(self._show_manual)
            layout.addWidget(btn_manual)
        else:
            btn_auto = QPushButton("🔄  Descargar e instalar automáticamente en segundo plano")
            btn_auto.clicked.connect(self._auto_install)
            layout.addWidget(btn_auto)

            btn_manual = QPushButton("📥  Ver instrucciones de instalación manual para Linux")
            btn_manual.clicked.connect(self._show_manual)
            layout.addWidget(btn_manual)

        btn_exit = QPushButton("✖  Salir")
        btn_exit.clicked.connect(self.reject)
        layout.addWidget(btn_exit)

    def _auto_install(self):
        self.action = self.ACTION_AUTO_INSTALL
        self.accept()

    def _open_powershell_install(self):
        if sys.platform != "win32":
            return
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-NoExit",
                    "-Command",
                    "winget install --id Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements; "
                    "Write-Host ''; "
                    "Write-Host '✅ Instalacion completada. Cierra esta ventana y vuelve a abrir la aplicacion.' -ForegroundColor Green",
                ],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error al abrir PowerShell",
                f"No se pudo abrir PowerShell:\n{e}\n\n"
                "Abre PowerShell manualmente y ejecuta:\n"
                "winget install --id Cloudflare.cloudflared",
            )
            return

        QMessageBox.information(
            self,
            "Instalando cloudflared",
            "Se abrió una ventana de PowerShell con el instalador.\n\n"
            "Espera a que termine la instalación y luego\n"
            "cierra y vuelve a abrir esta aplicación.",
        )
        self.reject()  # Cierra el diálogo y termina la app (el bucle en main sale por reject)

    def _show_manual(self):
        self.action = self.ACTION_MANUAL
        self.accept()


# ==============================
# Diálogo: instrucciones instalación manual de cloudflared
# ==============================
class CloudflaredManualDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Instalación manual de cloudflared")
        self.setModal(True)
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        if sys.platform == "win32":
            steps = QLabel(
                "<b>Pasos para instalar cloudflared manualmente en Windows:</b><br><br>"
                "1. Abre el navegador y ve a la URL de descarga (cópiala abajo).<br>"
                "2. Descarga <b>cloudflared-windows-amd64.msi</b> (instalador recomendado)<br>"
                "&nbsp;&nbsp;&nbsp;&nbsp;o <b>cloudflared-windows-amd64.exe</b> (ejecutable directo).<br>"
                "3. <b>Si descargaste el .msi:</b> ejecútalo y sigue el instalador normalmente.<br>"
                "4. <b>Si descargaste el .exe:</b> renómbralo a <b>cloudflared.exe</b> y colócalo<br>"
                "&nbsp;&nbsp;&nbsp;&nbsp;en una carpeta del PATH, por ejemplo:<br>"
                "&nbsp;&nbsp;&nbsp;&nbsp;<code>C:\\Windows\\System32\\cloudflared.exe</code><br>"
                "5. Cierra y vuelve a abrir esta aplicación.<br><br>"
                "<b>Alternativa con winget (ejecutar en cmd como administrador):</b>"
            )
            steps.setWordWrap(True)
            steps.setTextFormat(Qt.RichText)
            steps.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(steps)

            # URL de descarga — copiable
            url_label = QLabel("URL de descarga:")
            layout.addWidget(url_label)
            self.url_edit = QLineEdit("https://github.com/cloudflare/cloudflared/releases/latest")
            self.url_edit.setReadOnly(True)
            self.url_edit.setToolTip("Haz clic derecho → Copiar, o Ctrl+A y Ctrl+C")
            layout.addWidget(self.url_edit)

            # Comando winget — copiable
            cmd_label = QLabel("Comando winget:")
            layout.addWidget(cmd_label)
            self.cmd_edit = QLineEdit("winget install --id Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements")
            self.cmd_edit.setReadOnly(True)
            layout.addWidget(self.cmd_edit)
        else:
            steps = QLabel(
                "<b>Pasos para instalar cloudflared manualmente en Linux:</b><br><br>"
                "Puedes instalarlo usando el gestor de paquetes de tu distribución:<br><br>"
                "<b>En Debian/Ubuntu:</b><br>"
                "<code>curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared.deb</code><br><br>"
                "<b>Descarga directa del binario standalone:</b><br>"
                "1. Descarga el binario para tu arquitectura (cópiala abajo).<br>"
                "2. Renómbralo a <b>cloudflared</b> y dale permisos de ejecución: <code>chmod +x cloudflared</code><br>"
                "3. Muévelo a un directorio en tu PATH (ej. <code>/usr/local/bin</code>) o colócalo en el directorio de esta aplicación.<br>"
                "4. Cierra y vuelve a abrir esta aplicación.<br><br>"
                "<b>URL de descargas de GitHub:</b>"
            )
            steps.setWordWrap(True)
            steps.setTextFormat(Qt.RichText)
            steps.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(steps)

            # URL de descarga — copiable
            self.url_edit = QLineEdit("https://github.com/cloudflare/cloudflared/releases/latest")
            self.url_edit.setReadOnly(True)
            self.url_edit.setToolTip("Haz clic derecho → Copiar, o Ctrl+A y Ctrl+C")
            layout.addWidget(self.url_edit)

        layout.addSpacing(8)
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)


# ==============================
# Diálogo: Instalación de cloudflared
# ==============================
class CloudflaredInstallDialog(QDialog):
    finished = Signal(bool)  # emite True si la instalación fue exitosa
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Instalando cloudflared")
        self.setModal(True)
        self.resize(450, 200)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)  # Deshabilitar cerrar
        
        layout = QVBoxLayout(self)
        
        # Etiqueta de estado
        self.lbl_status = QLabel("Preparando instalación de cloudflared...")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        
        # Barra de progreso indeterminada
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # Modo indeterminado
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        
        # Etiqueta de información adicional
        self.lbl_info = QLabel("Esto puede tardar varios minutos. Por favor, espera...")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setAlignment(Qt.AlignCenter)
        self.lbl_info.setStyleSheet("color: #666; font-size: 9pt;")
        layout.addWidget(self.lbl_info)
        
        # Botón cancelar (opcional, pero mejor no permitir cancelar durante la instalación)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)
        
        # Iniciar la instalación en un hilo
        self._start_installation()
    
    def _start_installation(self):
        """Inicia la instalación en un hilo separado."""
        self.installer = CloudflaredInstaller()
        self.installer.status_update.connect(self._on_status_update)
        self.installer.finished.connect(self._on_installation_finished)
        
        thread = threading.Thread(target=self.installer.install, daemon=True)
        thread.start()
    
    def _on_status_update(self, message: str):
        """Actualiza el mensaje de estado."""
        self.lbl_status.setText(message)
    
    def _on_installation_finished(self, success: bool, message: str):
        """Se llama cuando la instalación termina."""
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        
        if success:
            self.lbl_status.setText("✅ Instalación completada exitosamente")
            self.lbl_status.setStyleSheet("color: green; font-weight: bold;")
            self.lbl_info.setText("cloudflared está listo para usar.")
            # Cerrar el diálogo después de un momento
            QTimer.singleShot(1500, self.accept)
        else:
            self.lbl_status.setText("❌ Error en la instalación")
            self.lbl_status.setStyleSheet("color: red; font-weight: bold;")
            self.lbl_info.setText(message)
            
            # Agregar botón para cerrar
            btn_layout = QHBoxLayout()
            btn_close = QPushButton("Cerrar")
            btn_close.clicked.connect(self.accept)  # Usar accept para continuar de todos modos
            btn_layout.addStretch(1)
            btn_layout.addWidget(btn_close)
            self.layout().addLayout(btn_layout)
        
        self.finished.emit(success)


# ==============================
# Worker: pedir comandas (hilo)
# ==============================
class ComandasFetcher(QObject):
    finished = Signal(list)      # emite lista de comandas
    error = Signal(str)

    def __init__(self, pin: str, base_url: str = "https://adm.premium-soft.com"):
        super().__init__()
        self.pin = pin
        self.base_url = base_url

    def fetch(self):
        url = f"{self.base_url}/Comandas/ConsultarLote?pin={self.pin}"
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise ValueError("Respuesta inesperada (no es lista)")
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(f"{e}")


# ==============================
# Worker: pedir agencias (hilo)
# ==============================
class AgenciasFetcher(QObject):
    finished = Signal(list)      # emite lista de agencias
    error = Signal(str)

    def __init__(self, pin: str, base_url: str = "https://adm.premium-soft.com"):
        super().__init__()
        self.pin = pin
        self.base_url = base_url

    def fetch(self):
        url = f"{self.base_url}/Agencias/ConsultarLote?pin={self.pin}"
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise ValueError("Respuesta inesperada (no es lista)")
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(f"{e}")


# ==============================
# Diálogo: Buscar agencias
# ==============================
class BuscarAgenciasDialog(QDialog):
    agencia_selected = Signal(dict)  # emite la agencia seleccionada

    def __init__(self, pin: str, parent=None, base_url: str = "https://adm.premium-soft.com"):
        super().__init__(parent)
        self.setWindowTitle("Buscar agencias")
        self.resize(600, 420)
        self.agencias_data = []  # Inicializar lista de agencias

        layout = QVBoxLayout(self)

        self.info = QLabel(f"Cargando agencias (PIN {pin})...")
        layout.addWidget(self.info)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["ID Agencia", "Nombre"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.btn_close = QPushButton("Cerrar")
        self.btn_close.clicked.connect(self.reject)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

        # Lanzar fetch en hilo
        self.fetcher = AgenciasFetcher(pin, base_url)
        t = threading.Thread(target=self.fetcher.fetch, daemon=True)
        self.fetcher.finished.connect(self._on_ok)
        self.fetcher.error.connect(self._on_error)
        t.start()

    def _on_ok(self, data: list):
        self.info.setText(f"{len(data)} agencias encontradas:")
        self.table.setRowCount(0)
        self.agencias_data = data  # Guardar los datos para acceso posterior

        for agencia in data:
            row = self.table.rowCount()
            self.table.insertRow(row)

            id_agencia = str(agencia.get("id_agencia", ""))
            nombre = str(agencia.get("nombre", ""))

            id_item = QTableWidgetItem(id_agencia)
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, id_item)

            nom_item = QTableWidgetItem(nombre)
            nom_item.setFlags(nom_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, nom_item)

        # Conectar doble clic en la tabla
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if row < len(self.agencias_data):
            agencia = self.agencias_data[row]
            self._select_and_close(agencia)

    def _on_error(self, err: str):
        self.info.setText("Error al cargar agencias.")
        QMessageBox.critical(self, "Error", f"No se pudieron obtener las agencias:\n{err}")

    def _select_and_close(self, agencia: dict):
        self.agencia_selected.emit(agencia)
        self.accept()


# ==============================
# Diálogo: Configuración (PIN)
# ==============================
class ConfigDialog(QDialog):
    saved = Signal(str)  # emite pin

    def __init__(self, current_pin: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Configuración v{APP_VERSION}")
        self.resize(450, 240)
        self.main_window = parent
        self.agencias_data = []

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Selector de servidor
        self.combo_server = QComboBox()
        for key, info in SERVERS.items():
            self.combo_server.addItem(info["label"], key)
        current_server = (self.main_window.state.get("api_server") or "adm") if self.main_window else "adm"
        idx = self.combo_server.findData(current_server)
        if idx >= 0:
            self.combo_server.setCurrentIndex(idx)
        self.combo_server.currentIndexChanged.connect(self._on_server_changed)
        form.addRow("Servidor:", self.combo_server)

        # Campo PIN
        self.ed_pin = QLineEdit(current_pin)
        self.ed_pin.setPlaceholderText("PIN para consultar comandas")
        self.ed_pin.textChanged.connect(self._on_pin_changed)
        form.addRow("PIN:", self.ed_pin)

        # Dropdown de agencias
        self.combo_agencia = QComboBox()
        self.combo_agencia.setPlaceholderText("Selecciona una agencia...")
        self.combo_agencia.addItem("— (sin agencia asignada)", None)
        form.addRow("Agencia:", self.combo_agencia)

        # Checkbox impresora virtual
        self.chk_virtual = QCheckBox()
        current_show_virtual = self.main_window.state.get("show_virtual_printer", False) if self.main_window else False
        self.chk_virtual.setChecked(current_show_virtual)
        form.addRow("Impresora Virtual:", self.chk_virtual)

        layout.addLayout(form)

        # Información de carga
        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        layout.addWidget(self.lbl_info)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("Guardar")
        self.btn_cancel = QPushButton("Cancelar")
        btns.addStretch(1)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_save)
        layout.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)

        # Cargar agencia actual si existe
        self._load_current_agencia()

        # Cargar agencias si hay PIN válido
        if current_pin and current_pin.strip().isdigit():
            self._load_agencias(current_pin)

    def _load_current_agencia(self):
        """Carga la agencia actual del estado para pre-seleccionarla"""
        if self.main_window:
            agencia = self.main_window.state.get("agencia")
            if agencia and agencia.get("id_agencia"):
                current_id = str(agencia.get("id_agencia", ""))
                # Esto se usará después de cargar las agencias
                self.current_agencia_id = current_id
            else:
                self.current_agencia_id = None
        else:
            self.current_agencia_id = None

    def _on_pin_changed(self, text: str):
        """Cuando cambia el PIN, cargar las agencias correspondientes"""
        pin = text.strip()
        if pin and pin.isdigit():
            self._load_agencias(pin)
        else:
            self.combo_agencia.clear()
            self.combo_agencia.addItem("— (sin agencia asignada)", None)
            self.lbl_info.setText("Ingresa un PIN válido para cargar agencias")

    def _on_server_changed(self, index: int):
        """Cuando cambia el servidor, recargar agencias"""
        pin = self.ed_pin.text().strip()
        if pin and pin.isdigit():
            self._load_agencias(pin)

    def _load_agencias(self, pin: str):
        """Carga las agencias desde el API"""
        self.lbl_info.setText(f"Cargando agencias (PIN {pin})...")
        self.combo_agencia.clear()
        self.combo_agencia.addItem("— (sin agencia asignada)", None)
        self.combo_agencia.setEnabled(False)

        server_key = self.combo_server.currentData() or "adm"
        base_url = SERVERS.get(server_key, SERVERS["adm"])["base_url"]
        # Lanzar fetch en hilo
        self.fetcher = AgenciasFetcher(pin, base_url)
        t = threading.Thread(target=self.fetcher.fetch, daemon=True)
        self.fetcher.finished.connect(self._on_agencias_loaded)
        self.fetcher.error.connect(self._on_agencias_error)
        t.start()

    def _on_agencias_loaded(self, data: list):
        """Cuando se cargan las agencias, agregarlas al dropdown"""
        self.agencias_data = data
        self.combo_agencia.clear()
        self.combo_agencia.addItem("— (sin agencia asignada)", None)
        
        for agencia in data:
            id_agencia = str(agencia.get("id_agencia", ""))
            nombre = str(agencia.get("nombre", ""))
            display_text = f"{nombre} (ID: {id_agencia})"
            self.combo_agencia.addItem(display_text, agencia)
        
        self.combo_agencia.setEnabled(True)
        self.lbl_info.setText(f"{len(data)} agencias cargadas")
        
        # Pre-seleccionar la agencia actual si existe
        if hasattr(self, 'current_agencia_id') and self.current_agencia_id:
            for i in range(self.combo_agencia.count()):
                agencia = self.combo_agencia.itemData(i)
                if agencia and str(agencia.get("id_agencia", "")) == self.current_agencia_id:
                    self.combo_agencia.setCurrentIndex(i)
                    break

    def _on_agencias_error(self, err: str):
        """Manejo de errores al cargar agencias"""
        self.combo_agencia.setEnabled(False)
        self.lbl_info.setText(f"Error al cargar agencias: {err}")

    def _save(self):
        pin = self.ed_pin.text().strip()
        if not pin.isdigit():
            QMessageBox.warning(self, "PIN inválido", "El PIN debe ser numérico.")
            return
        
        # Obtener la agencia seleccionada
        agencia_data = self.combo_agencia.currentData()
        agencia_to_save = None
        
        if agencia_data:
            agencia_to_save = {
                "id_agencia": str(agencia_data.get("id_agencia", "")),
                "nombre": str(agencia_data.get("nombre", ""))
            }
        
        server_key = self.combo_server.currentData() or "adm"

        # Guardar PIN, servidor, agencia y booleano de impresora virtual
        st = load_state()
        st["pin"] = pin
        st["agencia"] = agencia_to_save
        st["api_server"] = server_key
        st["show_virtual_printer"] = self.chk_virtual.isChecked()
        save_state(st)

        # Actualizar también en main_window si existe
        if self.main_window:
            self.main_window.state["pin"] = pin
            self.main_window.state["agencia"] = agencia_to_save
            self.main_window.state["api_server"] = server_key
            self.main_window.state["show_virtual_printer"] = self.chk_virtual.isChecked()
        
        self.saved.emit(pin)
        self.accept()


# ==============================
# Diálogo: Asignar comanda
# ==============================
class AssignComandaDialog(QDialog):
    assigned = Signal(dict)  # emite la comanda elegida

    def __init__(self, pin: str, parent=None, base_url: str = "https://adm.premium-soft.com"):
        super().__init__(parent)
        self.setWindowTitle("Asignar comanda")
        self.resize(600, 420)

        layout = QVBoxLayout(self)

        self.info = QLabel(f"Cargando comandas (PIN {pin})...")
        layout.addWidget(self.info)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["ID", "Código", "Nombre", "Acción"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        self.btn_close = QPushButton("Cerrar")
        self.btn_close.clicked.connect(self.reject)
        layout.addWidget(self.btn_close, alignment=Qt.AlignRight)

        # Lanzar fetch en hilo
        self.fetcher = ComandasFetcher(pin, base_url)
        t = threading.Thread(target=self.fetcher.fetch, daemon=True)
        self.fetcher.finished.connect(self._on_ok)
        self.fetcher.error.connect(self._on_error)
        t.start()

    def _on_ok(self, data: list):
        self.info.setText(f"{len(data)} comandas encontradas:")
        self.table.setRowCount(0)

        for com in data:
            row = self.table.rowCount()
            self.table.insertRow(row)

            id_item = QTableWidgetItem(str(com.get("id_comanda", "")))
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            cod_item = QTableWidgetItem(str(com.get("codigo", "")))
            cod_item.setFlags(cod_item.flags() & ~Qt.ItemIsEditable)
            nom_item = QTableWidgetItem(str(com.get("nombre", "")))
            nom_item.setFlags(nom_item.flags() & ~Qt.ItemIsEditable)

            self.table.setItem(row, 0, id_item)
            self.table.setItem(row, 1, cod_item)
            self.table.setItem(row, 2, nom_item)

            btn = QPushButton("Asignar")
            btn.clicked.connect(lambda _, c=com: self._assign_and_close(c))
            self.table.setCellWidget(row, 3, btn)

    def _on_error(self, err: str):
        self.info.setText("Error al cargar comandas.")
        QMessageBox.critical(self, "Error", f"No se pudieron obtener las comandas:\n{err}")

    def _assign_and_close(self, comanda: dict):
        self.assigned.emit(comanda)
        self.accept()


# ==============================
# Icono de bandeja del sistema
# ==============================
def _make_tray_icon() -> QIcon:
    px = QPixmap(32, 32)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#1a6fa8"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 28, 28, 6, 6)
    p.setPen(QColor("white"))
    p.setFont(QFont("Arial", 10, QFont.Weight.Bold))
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "srv")
    p.end()
    return QIcon(px)


# ==============================
# Diálogo: Selección de servidor (primer arranque)
# ==============================
class ServerSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seleccionar servidor")
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self.resize(360, 160)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Selecciona el servidor al que pertenece tu cuenta:"))

        self.combo = QComboBox()
        for key, info in SERVERS.items():
            self.combo.addItem(info["label"], key)
        layout.addWidget(self.combo)

        btn = QPushButton("Continuar")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

    def selected_key(self) -> str:
        return self.combo.currentData()


# ==============================
# Captura de Logs de Consola
# ==============================
class ConsoleLogStream(QObject):
    message_written = Signal(str)

    def __init__(self, original_stream, parent=None):
        super().__init__(parent)
        self.original_stream = original_stream
        self._lock = threading.Lock()
        self._in_write = False

    def write(self, text):
        if self.original_stream:
            self.original_stream.write(text)
        with self._lock:
            if self._in_write:
                return
            self._in_write = True
            try:
                self.message_written.emit(text)
            except Exception:
                pass
            finally:
                self._in_write = False

    def flush(self):
        if self.original_stream:
            self.original_stream.flush()


# ==============================
# Ventana principal
# ==============================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Servidor de Comandas e Impresión v{APP_VERSION}")
        self.resize(950, 560)

        # Redireccionar consola
        self._log_buffer = []
        self.stdout_stream = ConsoleLogStream(sys.stdout, self)
        self.stderr_stream = ConsoleLogStream(sys.stderr, self)
        self.stdout_stream.message_written.connect(self._append_log)
        self.stderr_stream.message_written.connect(self._append_log)
        sys.stdout = self.stdout_stream
        sys.stderr = self.stderr_stream

        # Estado persistente - cargar al inicializar
        self.state = load_state()
        _log_debug(f"Estado cargado al inicializar: {len(self.state.get('printers', []))} impresoras, {len(self.state.get('assignments_by_comanda', {}))} asignaciones")

        # Primer arranque: pedir servidor si no está configurado
        if not self.state.get("api_server"):
            dlg = ServerSelectDialog(self)
            dlg.exec()
            self.state["api_server"] = dlg.selected_key()
            save_state(self.state)

        # Verificar si hay impresoras problemáticas y mostrar advertencia
        self._check_and_warn_about_problematic_printers()

        # UI raíz
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Barra superior
        topbar = QHBoxLayout()
        self.btn_config = QPushButton("⚙️ Configuración")
        self.btn_config.clicked.connect(self.open_config)

        self.lbl_pin = QLabel(f"PIN actual: {self.state['pin']}")
        self.lbl_pin.setToolTip("Este PIN se usa para consultar el listado de comandas")

        _srv_key = self.state.get("api_server") or "adm"
        self.lbl_server = QLabel(f"Servidor: {SERVERS.get(_srv_key, SERVERS['adm'])['label']}")
        self.lbl_server.setToolTip("Servidor API activo (cambia en Configuración)")

        self.lbl_version = QLabel(f"v{APP_VERSION}")
        self.lbl_version.setStyleSheet("color: #666; font-size: 10pt; font-weight: bold;")
        self.lbl_version.setToolTip("Versión de la aplicación")

        topbar.addWidget(self.btn_config)
        topbar.addStretch(1)
        self.btn_server = QPushButton("▶️ Iniciar servidor")
        self.btn_server.setCheckable(True)
        self.btn_server.toggled.connect(self.toggle_server)
        topbar.addWidget(self.btn_server)
        topbar.addWidget(self.lbl_server)
        topbar.addWidget(self.lbl_pin)
        topbar.addWidget(self.lbl_version)
        root.addLayout(topbar)

        # Tabs
        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs)

        # --- Tab 1: Mis impresoras ---
        self.tab_list = QWidget()
        # self.tabs.addTab(self.tab_list, "Mis impresoras")

        list_layout = QVBoxLayout(self.tab_list)
        list_top = QHBoxLayout()
        lbl_list_info = QLabel("Impresoras agregadas para impresión rápida:")
        list_top.addWidget(lbl_list_info)
        list_top.addStretch(1)
        btn_go_search = QPushButton("🔍 Buscar impresoras")
        btn_go_search.clicked.connect(self.go_to_search_tab)
        list_top.addWidget(btn_go_search)
        list_layout.addLayout(list_top)

        self.table_printers = QTableWidget(0, 5, self.tab_list)
        self.table_printers.setHorizontalHeaderLabels(["Nombre", "Asignación", "Acciones", "Probar", "Eliminar"])
        self.table_printers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_printers.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_printers.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_printers.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_printers.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        list_layout.addWidget(self.table_printers)

        # --- Tab 2: Buscar impresoras ---
        self.tab_search = QWidget()
        # self.tabs.addTab(self.tab_search, "Buscar impresoras")

        search_layout = QVBoxLayout(self.tab_search)
        search_top = QHBoxLayout()
        self.lbl_search_info = QLabel("Buscando impresoras...")
        search_top.addWidget(self.lbl_search_info)
        search_top.addStretch(1)
        btn_search_refresh = QPushButton("🔄 Actualizar")
        btn_search_refresh.clicked.connect(self.refresh_printer_search)
        search_top.addWidget(btn_search_refresh)
        search_layout.addLayout(search_top)

        self.table_search = QTableWidget(0, 2, self.tab_search)
        self.table_search.setHorizontalHeaderLabels(["Impresora", "Acción"])
        self.table_search.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_search.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        search_layout.addWidget(self.table_search)

        # --- Tab 3: Comandas (comanda -> impresora) ---
        self.tab_cmd = QWidget()
        self.tabs.addTab(self.tab_cmd, "Comandas")

        cmd_layout = QVBoxLayout(self.tab_cmd)
        self.lbl_cmd_info = QLabel("Carga las comandas y asigna una impresora por cada una.")
        cmd_layout.addWidget(self.lbl_cmd_info)

        self.table_cmd = QTableWidget(0, 3, self.tab_cmd)
        self.table_cmd.setHorizontalHeaderLabels(["ID", "Nombre", "Impresora"])
        self.table_cmd.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_cmd.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_cmd.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        cmd_layout.addWidget(self.table_cmd)

        cmd_btns = QHBoxLayout()
        self.btn_cmd_refresh = QPushButton("🔄 Cargar comandas")
        self.btn_cmd_refresh.clicked.connect(self.refresh_comandas_tab)
        cmd_btns.addWidget(self.btn_cmd_refresh)
        cmd_btns.addStretch(1)
        cmd_layout.addLayout(cmd_btns)

        # --- Tab 4: Logs ---
        self.tab_logs = QWidget()
        self.tabs.addTab(self.tab_logs, "Consola / Logs")

        logs_layout = QVBoxLayout(self.tab_logs)
        logs_top = QHBoxLayout()
        lbl_logs_info = QLabel("Registro de eventos de la consola en tiempo real:")
        logs_top.addWidget(lbl_logs_info)
        logs_top.addStretch(1)
        
        btn_clear_logs = QPushButton("🗑️ Limpiar")
        btn_clear_logs.clicked.connect(self._clear_logs)
        logs_top.addWidget(btn_clear_logs)
        
        btn_copy_logs = QPushButton("📋 Copiar")
        btn_copy_logs.clicked.connect(self._copy_logs)
        logs_top.addWidget(btn_copy_logs)
        
        logs_layout.addLayout(logs_top)

        self.txt_logs = QPlainTextEdit(self.tab_logs)
        self.txt_logs.setReadOnly(True)
        # Usar fuente monoespaciada para logs
        font = QFont("Courier New" if sys.platform == "win32" else "Monospace", 10)
        self.txt_logs.setFont(font)
        logs_layout.addWidget(self.txt_logs)
        
        # Volcar buffer inicial si existe
        if hasattr(self, "_log_buffer") and self._log_buffer:
            for text in self._log_buffer:
                self.txt_logs.appendPlainText(text)
            self._log_buffer = None

        # Cargar impresoras y comandas al iniciar
        self.reload_printers_table()
        self.refresh_comandas_tab()

        # Servidor y Túnel Cloudflare
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.cloudflared_process: Optional[subprocess.Popen] = None
        self.cloudflared_url: Optional[str] = None
        self.cloudflared_thread: Optional[threading.Thread] = None
        self._cloudflared_stop_event = threading.Event()
        self._cloudflared_lock = threading.Lock()
        self._cloudflare_url_synced: bool = False

        self._setup_tray()

    # ---------- Configuración ----------
    def open_config(self):
        dlg = ConfigDialog(self.state["pin"], self)
        dlg.saved.connect(self._apply_pin)
        dlg.exec()

    def _apply_pin(self, new_pin: str):
        self.state = load_state()
        _log_debug(f"Auto-save: PIN actualizado a '{new_pin}'")
        self.lbl_pin.setText(f"PIN actual: {self.state['pin']}")
        srv_key = self.state.get("api_server") or "adm"
        self.lbl_server.setText(f"Servidor: {SERVERS.get(srv_key, SERVERS['adm'])['label']}")

        # Recargar comandas automáticamente con el nuevo PIN
        self.refresh_comandas_tab()
        
        # Recargar listas de impresoras para reflejar cambios en la impresora virtual
        self.refresh_printer_search()
        self.reload_printers_table()

        # Si el túnel está activo, reenviar la URL con el nuevo PIN/agencia
        if self.cloudflared_url and self.cloudflared_process and self.cloudflared_process.poll() is None:
            self._cloudflare_url_synced = self._send_cloudflare_url_to_server(self.cloudflared_url)

        QMessageBox.information(self, "Configuración", "Configuración guardada correctamente.")

    # ---------- Bandeja del sistema ----------
    def _setup_tray(self):
        self._tray = QSystemTrayIcon(_make_tray_icon(), self)
        self._tray.setToolTip("Servidor de Comandas")
        menu = QMenu()
        act_open = menu.addAction("Abrir")
        act_open.triggered.connect(self._show_window)
        menu.addSeparator()
        act_quit = menu.addAction("Salir")
        act_quit.triggered.connect(self._quit_app)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _quit_app(self):
        self.stop_server()
        QApplication.quit()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Servidor de Comandas",
            "Activo en segundo plano.\nDoble clic para abrir.",
            QSystemTrayIcon.MessageIcon.Information, 3000,
        )

    # ---------- Servidor HTTPS local ----------
    def toggle_server(self, checked: bool):
        if checked:
            try:
                self.start_server()
                self.btn_server.setText("⏹️ Detener servidor")
            except Exception as e:
                self.btn_server.setChecked(False)
                QMessageBox.critical(self, "Servidor", f"No se pudo iniciar:\n{e}")
        else:
            self.stop_server()
            self.btn_server.setText("▶️ Iniciar servidor")

    def start_server(self):
        # Cloudflare está enviando el tráfico al puerto 8443
        host = "0.0.0.0" 
        port = 8443  # IMPORTANTE: Debe coincidir con el comando cloudflared

        handler = self.make_handler()
        
        # Creamos un servidor HTTP SIMPLE (sin SSL/Certificados)
        self.httpd = ThreadingHTTPServer((host, port), handler)

        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.http_thread.start()
        
        _log_debug(f"Servidor iniciado en modo HTTP para Túnel en puerto {port}")
        
        # Iniciar watchdog de cloudflared: inicia el túnel y supervisa periódicamente su disponibilidad
        self._cloudflared_stop_event.clear()
        self._cloudflare_url_synced = False
        self.cloudflared_thread = threading.Thread(target=self._cloudflared_watchdog, daemon=True)
        self.cloudflared_thread.start()

    def _cloudflared_watchdog(self):
        """Supervisa continuamente el ciclo de vida del túnel Cloudflare.
        Valida periódicamente que siga abierto y respondiendo; en caso de caída o expiración,
        genera automáticamente otro túnel y actualiza su URL en el servidor.
        """
        _log_debug("Watchdog de Cloudflare iniciado")
        print("🛡️ Iniciando monitor de túnel Cloudflare...")

        # 1. Lanzamiento inicial del túnel
        with self._cloudflared_lock:
            if not self._cloudflared_stop_event.is_set():
                self._start_cloudflared_tunnel()

        consecutive_failures = 0
        max_failures = 2  # Reintentos fallidos antes de regenerar el túnel

        while not self._cloudflared_stop_event.is_set():
            # Esperar el intervalo configurado
            if self._cloudflared_stop_event.wait(timeout=CLOUDFLARE_HEALTH_CHECK_INTERVAL):
                break

            if not self.httpd:
                break

            with self._cloudflared_lock:
                if self._cloudflared_stop_event.is_set():
                    break

                ts = time.strftime("%H:%M:%S")
                print(f"🔍 [{ts}] [Watchdog] Verificando estado del túnel Cloudflare...")

                # Comprobación A: El proceso sigue activo
                proc_alive = (
                    self.cloudflared_process is not None
                    and self.cloudflared_process.poll() is None
                )

                if not proc_alive or not self.cloudflared_url:
                    consecutive_failures += 1
                    print(f"⚠️ [{ts}] [Watchdog] El proceso de cloudflared no está activo ({consecutive_failures}/{max_failures})")
                    _log_debug("Watchdog: el proceso de cloudflared no está corriendo o no tiene URL.")
                else:
                    # Comprobación B: Disponibilidad HTTP real a través del túnel público
                    is_healthy, status_msg = self._check_tunnel_health(self.cloudflared_url)
                    if is_healthy:
                        consecutive_failures = 0
                        print(f"🟢 [{ts}] [Watchdog] Túnel Cloudflare activo y disponible: {self.cloudflared_url} ({status_msg})")
                        _log_debug(f"Watchdog: túnel {self.cloudflared_url} verificado y funcionando: {status_msg}")
                        # Si quedó una sincronización pendiente con el backend, reintentarla
                        if not self._cloudflare_url_synced:
                            print(f"📤 [{ts}] Sincronizando URL de Cloudflare pendiente con el servidor...")
                            self._cloudflare_url_synced = self._send_cloudflare_url_to_server(self.cloudflared_url)
                    else:
                        consecutive_failures += 1
                        print(f"⚠️ [{ts}] [Watchdog] Falló comprobación del túnel ({consecutive_failures}/{max_failures}): {status_msg}")
                        _log_debug(f"Watchdog: fallo al validar túnel {self.cloudflared_url} ({consecutive_failures}/{max_failures}): {status_msg}")

                # Si se superó el umbral de fallos consecutivos, regenerar el túnel
                if consecutive_failures >= max_failures:
                    print(f"🔄 [{ts}] [Watchdog] El túnel no responde o ha expirado. Regenerando nuevo túnel...")
                    _log_debug("Watchdog: regenerando túnel Cloudflare...")
                    self._stop_cloudflared_process()

                    # Pausa breve antes de crear el nuevo proceso
                    if self._cloudflared_stop_event.wait(timeout=2):
                        break

                    success = self._start_cloudflared_tunnel()
                    if success:
                        print(f"✅ [{time.strftime('%H:%M:%S')}] [Watchdog] Nuevo túnel generado y registrado: {self.cloudflared_url}")
                        consecutive_failures = 0
                    else:
                        print(f"❌ [{time.strftime('%H:%M:%S')}] [Watchdog] No se pudo obtener la URL del nuevo túnel. Se reintentará en el siguiente ciclo.")
                        consecutive_failures = max_failures

        _log_debug("Watchdog de Cloudflare detenido.")

    def _check_tunnel_health(self, url: str) -> Tuple[bool, str]:
        """Realiza una petición HTTP al endpoint /healthz del túnel público para verificar que esté vivo."""
        if not url:
            return False, "URL no configurada"
        health_url = f"{url.rstrip('/')}/healthz"
        try:
            resp = requests.get(
                health_url,
                timeout=CLOUDFLARE_HEALTH_CHECK_TIMEOUT,
                headers={
                    "User-Agent": "ComandarServer-Watchdog/1.0",
                    "cf-tunnel-skip-bypass": "true"
                }
            )
            if resp.status_code == 200:
                return True, f"HTTP {resp.status_code} OK"
            _log_debug(f"Watchdog: código HTTP {resp.status_code} desde {health_url}")
            return False, f"HTTP {resp.status_code}"
        except requests.exceptions.Timeout:
            return False, f"Timeout ({CLOUDFLARE_HEALTH_CHECK_TIMEOUT}s)"
        except requests.exceptions.ConnectionError as ce:
            return False, f"Error de conexión ({type(ce).__name__})"
        except Exception as e:
            _log_debug(f"Watchdog: error al validar {health_url}: {e}")
            return False, str(e)

    def _start_cloudflared_tunnel(self) -> bool:
        """Ejecuta cloudflared tunnel, extrae la nueva URL y la envía al servidor."""
        try:
            bin_path = _find_cloudflared_bin() or "cloudflared"
            cmd = [bin_path, "tunnel", "--url", "http://localhost:8443"]
            _log_debug(f"Ejecutando comando: {' '.join(cmd)}")
            
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW
            
            self.cloudflared_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                creationflags=creation_flags
            )
            
            # Leer la salida para encontrar la URL generada
            url_pattern = re.compile(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com')
            url_found = False
            max_lines = 150
            line_count = 0
            start_time = time.time()
            max_wait_seconds = 40
            
            while (
                self.cloudflared_process.poll() is None
                and not url_found
                and line_count < max_lines
                and not self._cloudflared_stop_event.is_set()
            ):
                if time.time() - start_time > max_wait_seconds:
                    _log_debug("Timeout alcanzado esperando URL de cloudflared")
                    break

                line = self.cloudflared_process.stdout.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                
                line_count += 1
                match = url_pattern.search(line)
                if match:
                    self.cloudflared_url = match.group(0)
                    url_found = True
                    print(f"URL del túnel Cloudflare encontrada: {self.cloudflared_url}")
                    _log_debug(f"URL del túnel Cloudflare: {self.cloudflared_url}")
                    break
                
                _log_debug(f"Cloudflared output: {line.strip()}")
            
            if url_found and self.cloudflared_url:
                # Iniciar hilo para drenar stdout continuamente y evitar que el búfer de tubería bloquee cloudflared
                proc = self.cloudflared_process
                def _drain_stdout():
                    try:
                        if proc and proc.stdout:
                            for l in iter(proc.stdout.readline, ''):
                                if not l:
                                    break
                                _log_debug(f"Cloudflared: {l.strip()}")
                    except Exception:
                        pass
                
                t_drain = threading.Thread(target=_drain_stdout, daemon=True)
                t_drain.start()

                # Enviar la URL al servidor remoto y registrar sincronización
                self._cloudflare_url_synced = self._send_cloudflare_url_to_server(self.cloudflared_url)
                return True
            else:
                _log_debug("No se encontró la URL del túnel en la salida de cloudflared")
                print("ADVERTENCIA: No se pudo extraer la URL del túnel Cloudflare de la salida")
                self._stop_cloudflared_process()
                return False
                
        except FileNotFoundError:
            error_msg = "cloudflared no se encuentra en el PATH. Por favor, instálalo."
            print(f"ERROR: {error_msg}")
            _log_debug(error_msg)
            self._stop_cloudflared_process()
            return False
        except Exception as e:
            error_msg = f"Error al ejecutar cloudflared: {e}"
            print(f"ERROR: {error_msg}")
            _log_debug(error_msg)
            _log_exception("Error en _start_cloudflared_tunnel", e)
            self._stop_cloudflared_process()
            return False

    def _stop_cloudflared_process(self):
        """Detiene el proceso de cloudflared de forma segura."""
        try:
            if self.cloudflared_process:
                _log_debug("Terminando proceso cloudflared...")
                self.cloudflared_process.terminate()
                try:
                    self.cloudflared_process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.cloudflared_process.kill()
                    self.cloudflared_process.wait(timeout=2)
                _log_debug("Proceso cloudflared terminado exitosamente.")
        except Exception as e:
            _log_debug(f"Error al terminar cloudflared: {e}")
        finally:
            self.cloudflared_process = None
            self.cloudflared_url = None

    def _send_cloudflare_url_to_server(self, url: str) -> bool:
        """Envía la URL de cloudflared y la agencia al servidor mediante API"""
        try:
            pin = self.state.get("pin", "")
            if not pin:
                _log_debug("No hay PIN configurado para enviar la URL de cloudflared")
                print("ADVERTENCIA: No hay PIN configurado, no se puede enviar la URL al servidor")
                return False
            
            # Obtener el id_agencia del estado
            agencia = self.state.get("agencia")
            id_agencia = None
            if agencia:
                id_agencia_raw = agencia.get("id_agencia", "")
                if id_agencia_raw:
                    id_agencia = str(id_agencia_raw).strip()
                    if not id_agencia:
                        id_agencia = None
                else:
                    id_agencia = None
            
            # Construir la URL del endpoint API
            endpoint_url = get_base_url(self.state) + "/Configuracion/GuardarUrlCloudflare"
            
            # Datos a enviar al API
            data = {
                "pin": str(pin),
                "url": str(url),
                "tipo": "impresion"
            }
            
            if id_agencia:
                data["id_agencia"] = str(id_agencia)
            else:
                data["id_agencia"] = ""
            
            # Debug: Mostrar información detallada
            print("📤 Enviando URL de cloudflared al servidor:")
            print(f"   Endpoint: {endpoint_url}")
            print(f"   PIN: {pin}")
            print(f"   URL: {url}")
            print(f"   ID Agencia: {id_agencia or 'NO CONFIGURADO (default)'}")
            _log_debug(f"Enviando URL de cloudflared al servidor: {endpoint_url}")
            _log_debug(f"Datos completos: {data}")
            
            # Enviar POST al API
            response = requests.post(
                endpoint_url,
                data=data,
                timeout=15,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            response.raise_for_status()
            
            # Verificar respuesta
            response_text = response.text.strip()
            print(f"   Respuesta servidor: {response_text}")
            
            if response_text.lower() in ("true", "1", "ok") or '"success":true' in response_text.lower():
                print("✅ URL de cloudflared guardada exitosamente en el servidor")
                _log_debug("Respuesta exitosa del servidor al guardar URL Cloudflare")
                return True
            else:
                print(f"⚠️ Respuesta del servidor al guardar URL: {response_text}")
                _log_debug(f"Respuesta del servidor: {response_text}")
                return False
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Error al enviar URL de cloudflared al servidor: {e}"
            print(f"ERROR: {error_msg}")
            _log_debug(error_msg)
            _log_exception("Error en _send_cloudflare_url_to_server", e)
            return False
        except Exception as e:
            error_msg = f"Error inesperado al enviar URL de cloudflared: {e}"
            print(f"ERROR: {error_msg}")
            _log_debug(error_msg)
            _log_exception("Error inesperado en _send_cloudflare_url_to_server", e)
            return False
        
    def stop_server(self):
        # 1. Señalizar detención al monitor/watchdog
        self._cloudflared_stop_event.set()

        # 2. Detener servidor HTTP
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
                self.httpd = None
                _log_debug("Servidor HTTP detenido.")
        except Exception as e:
            _log_debug(f"Error al detener servidor HTTP: {e}")
        
        # 3. Detener el proceso de cloudflared
        with self._cloudflared_lock:
            self._stop_cloudflared_process()

    def make_handler(self):
        main_window = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                _log_debug("HTTP " + fmt % args)
            
            def log_request(self, code='-', size='-'):
                """Log de peticiones entrantes para debugging"""
                _log_debug(f"Request: {self.command} {self.path} - Status: {code}")
            
            def address_string(self):
                """Obtener dirección del cliente para logging"""
                return self.client_address[0]

            def do_OPTIONS(self):
                try:
                    origin = self.headers.get('Origin', '*')
                    requested_methods = self.headers.get('Access-Control-Request-Method', 'GET, POST, OPTIONS, PUT, DELETE')
                    requested_headers = self.headers.get('Access-Control-Request-Headers', 'Content-Type, Accept, Authorization, cf-tunnel-skip-bypass, X-Requested-With')
                    
                    _log_debug(f"OPTIONS preflight desde origen: {origin}, métodos: {requested_methods}, headers: {requested_headers}")
                    
                    self.send_response(200)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Authorization, cf-tunnel-skip-bypass, X-Requested-With")
                    self.send_header("Access-Control-Expose-Headers", "Content-Type, Content-Length")
                    self.send_header("Access-Control-Max-Age", "86400")
                    self.send_header("Access-Control-Allow-Credentials", "false")
                    self.send_header("Content-Length", "0")
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as net_err:
                    _log_debug(f"Cliente desconectado durante OPTIONS: {net_err}")

            def do_GET(self):
                try:
                    origin = self.headers.get('Origin', 'no-origin')
                    _log_debug(f"GET {self.path} desde origen: {origin}, cliente: {self.client_address[0]}")
                    
                    parsed = urlparse(self.path)
                    if parsed.path == "/healthz":
                        self._send(200, {"status": "ok"})
                        return
                    if parsed.path == "/status":
                        printers = discover_printers()
                        assignments = main_window.state.get("assignments_by_comanda", {})
                        self._send(200, {
                            "printers": printers,
                            "assignments": assignments,
                            "pin": main_window.state.get("pin", ""),
                            "sumatra": _find_sumatra() or "no encontrado"
                        })
                        return
                    if parsed.path == "/printers":
                        printers = discover_printers()
                        self._send(200, {"printers": printers})
                        return
                    if parsed.path == "/print":
                        from urllib.parse import parse_qs
                        q = parse_qs(parsed.query)
                        comanda_id = (q.get("comanda_id", [""])[0] or "").strip()
                        id_operti = (q.get("id_operti", [""])[0] or "").strip()
                        method = (q.get("method", ["auto"])[0] or "auto").strip().lower()
                        override_printer = (q.get("printer", [""])[0] or "").strip() or None
                        _log_debug(f"HTTP /print comanda_id={comanda_id} id_operti={id_operti} method={method} override_printer={override_printer}")
                        if not comanda_id or not id_operti:
                            self._send(400, {"error": "comanda_id e id_operti requeridos"})
                            return
                        
                        printer = override_printer or main_window.state.get("assignments_by_comanda", {}).get(comanda_id)
                        
                        if not printer:
                            for p_name, com in main_window.state.get("assignments", {}).items():
                                if com and str(com.get("id_comanda", "")).strip() == str(comanda_id).strip():
                                    printer = p_name
                                    _log_debug(f"HTTP /print fallback encontrado en assignments: comanda {comanda_id} -> impresora {printer}")
                                    break
                                    
                        _log_debug(f"HTTP /print printer seleccionada: {printer}")
                        if not printer:
                            self._send(409, {"error": "comanda sin impresora asignada", "comanda_id": comanda_id})
                            return
                        url = build_ticket_url(main_window.state["pin"], comanda_id, id_operti, get_base_url(main_window.state))
                        _log_debug(f"HTTP /print URL generada: {url}")
                        try:
                            if method == "sumatra":
                                print_pdf_from_url_sumatra_only(url, printer)
                            else:
                                print_pdf_from_url_auto(url, printer)
                            self._send(200, {"status": "sent", "printer": printer})
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as net_err:
                            _log_debug(f"Cliente se desconectó durante o después de la impresión: {net_err}")
                        except Exception as e:
                            _log_exception("HTTP /print fallo", e)
                            self._send(500, {"error": str(e)})
                        return

                    self._send(404, {"error": "not found"})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as net_err:
                    _log_debug(f"Cliente desconectado durante petición HTTP GET: {net_err}")
                except Exception as e:
                    _log_exception("HTTP handler error", e)
                    self._send(500, {"error": str(e)})

            def _send(self, code: int, obj: dict):
                try:
                    body = json.dumps(obj).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Authorization, cf-tunnel-skip-bypass, X-Requested-With")
                    self.send_header("Access-Control-Expose-Headers", "Content-Type, Content-Length")
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as net_err:
                    _log_debug(f"Cliente desconectado antes de recibir respuesta HTTP {code}: {net_err}")


        return Handler

    # ---------- Navegación ----------
    def go_to_search_tab(self):
        self.tabs.setCurrentWidget(self.tab_search)
        self.refresh_printer_search()

    # ---------- Buscar impresoras ----------
    def refresh_printer_search(self):
        printers = discover_printers()
        self.table_search.setRowCount(0)
        if not printers:
            self.lbl_search_info.setText("No se encontraron impresoras.")
        else:
            self.lbl_search_info.setText(f"Se encontraron {len(printers)} impresoras.")

        for name in printers:
            self._add_search_row(name)

    def _add_search_row(self, printer_name: str):
        row = self.table_search.rowCount()
        self.table_search.insertRow(row)

        name_item = QTableWidgetItem(printer_name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        self.table_search.setItem(row, 0, name_item)

        btn_add = QPushButton("Agregar")
        btn_add.clicked.connect(lambda _, n=printer_name: self.add_printer_to_list(n))
        self.table_search.setCellWidget(row, 1, btn_add)

    # ---------- Tabla principal ----------
    def reload_printers_table(self):
        self.table_printers.setRowCount(0)
        for name in self.state["printers"]:
            self._add_printer_row(name)

    def _add_printer_row(self, printer_name: str):
        row = self.table_printers.rowCount()
        self.table_printers.insertRow(row)

        # Col 0: nombre
        name_item = QTableWidgetItem(printer_name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        self.table_printers.setItem(row, 0, name_item)

        # Col 1: asignación
        asg_item = QTableWidgetItem(self._assignment_text(printer_name))
        asg_item.setFlags(asg_item.flags() & ~Qt.ItemIsEditable)
        self.table_printers.setItem(row, 1, asg_item)

        # Col 2: Asignar comanda
        btn_assign = QPushButton("Asignar comanda")
        btn_assign.clicked.connect(lambda _, r=row: self.open_assign_dialog_for_row(r))
        self.table_printers.setCellWidget(row, 2, btn_assign)

        # Col 3: Imprimir
        btn_print = QPushButton("Imprimir")
        btn_print.clicked.connect(lambda _, r=row: self.print_ticket_for_row(r))
        self.table_printers.setCellWidget(row, 3, btn_print)

        # Col 4: Eliminar
        btn_delete = QPushButton("Eliminar")
        btn_delete.setStyleSheet("color: #b00;")
        btn_delete.clicked.connect(lambda _, r=row: self.delete_printer_row(r))
        self.table_printers.setCellWidget(row, 4, btn_delete)

    def _assignment_text(self, printer_name: str) -> str:
        com = self.state["assignments"].get(printer_name)
        if not com:
            return "—"
        return f'{com.get("nombre", "??")} (id {com.get("id_comanda", "?")})'

    def _auto_save_comanda_assignment(self, comanda_id: str, printer_text: str):
        """Guarda automáticamente la asignación de comanda a impresora cuando cambia el dropdown."""
        try:
            # Limpiar el texto de la impresora
            printer_name = printer_text.strip() if printer_text and not printer_text.startswith("—") else ""
            
            # Actualizar el estado
            if printer_name:
                self.state.setdefault("assignments_by_comanda", {})[comanda_id] = printer_name
                _log_debug(f"Auto-save: Comanda {comanda_id} asignada a impresora '{printer_name}'")
            else:
                # Si no hay impresora seleccionada, remover la asignación
                if comanda_id in self.state.get("assignments_by_comanda", {}):
                    del self.state["assignments_by_comanda"][comanda_id]
                    _log_debug(f"Auto-save: Comanda {comanda_id} sin asignar")
            
            # Guardar el estado
            save_state(self.state)
            
        except Exception as e:
            _log_exception("Error en auto-save de asignación de comanda", e)

    def _check_and_warn_about_problematic_printers(self):
        """Verifica si hay impresoras problemáticas y muestra advertencia si es necesario."""
        try:
            all_printers = discover_printers()
            problematic_count = 0
            
            # Verificar si se detectaron impresoras pero se filtraron
            if all_printers:
                # Si tenemos impresoras locales, todo está bien
                _log_debug(f"Impresoras locales detectadas: {all_printers}")
            else:
                # Verificar si hay impresoras instaladas pero filtradas
                try:
                    if win32print:
                        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
                        all_installed = [p[2] for p in win32print.EnumPrinters(flags)]
                    else:
                        all_installed = _discover_printers_linux()
                    for printer in all_installed:
                        if not _is_safe_printer(printer):
                            problematic_count += 1
                            _log_debug(f"Impresora problemática detectada: {printer}")
                except Exception:
                    pass
            
            if problematic_count > 0:
                _log_debug(f"Se detectaron {problematic_count} impresoras problemáticas que fueron filtradas")
                
        except Exception as e:
            _log_exception("Error verificando impresoras problemáticas", e)

    def add_printer_to_list(self, printer_name: str):
        discovered_count = discover_printers().count(printer_name)
        current_count = self.state["printers"].count(printer_name)
        if current_count >= discovered_count and discovered_count > 0:
            QMessageBox.information(self, "Ya existe", f"La impresora \"{printer_name}\" ya está en la lista.")
            self.tabs.setCurrentWidget(self.tab_list)
            return

        # Validar que la impresora sea segura antes de agregar
        if not _is_safe_printer(printer_name):
            QMessageBox.warning(
                self, 
                "Impresora no segura", 
                f"La impresora '{printer_name}' parece ser una impresora remota o insegura.\n\n"
                "Por seguridad, solo se permiten impresoras locales.\n"
                "Verifica que sea una impresora USB o local conectada físicamente a tu computadora."
            )
            return

        self.state["printers"].append(printer_name)
        save_state(self.state)
        _log_debug(f"Auto-save: Impresora '{printer_name}' agregada a la lista")

        self._add_printer_row(printer_name)
        self.tabs.setCurrentWidget(self.tab_list)

    def open_assign_dialog_for_row(self, row: int):
        name_item = self.table_printers.item(row, 0)
        if not name_item:
            return
        printer_name = name_item.text()

        dlg = AssignComandaDialog(self.state["pin"], self, base_url=get_base_url(self.state))
        dlg.assigned.connect(lambda com, p=printer_name, r=row: self._set_assignment(p, r, com))
        dlg.exec()

    def _set_assignment(self, printer_name: str, row: int, comanda: dict):
        self.state["assignments"][printer_name] = comanda
        
        # Sincronizar assignments_by_comanda
        if comanda and "id_comanda" in comanda:
            com_id = str(comanda["id_comanda"]).strip()
            if com_id:
                self.state.setdefault("assignments_by_comanda", {})[com_id] = printer_name
                _log_debug(f"Sincronizado: Comanda {com_id} asignada a impresora '{printer_name}' en assignments_by_comanda")
                
        save_state(self.state)

        self.table_printers.item(row, 1).setText(self._assignment_text(printer_name))
        QMessageBox.information(
            self, "Asignado",
            f'Impresora: {printer_name}\nComanda asignada: {comanda.get("nombre", "")}'
        )

    # ---------- Tab Comandas ----------
    def refresh_comandas_tab(self):
        pin = self.state["pin"]
        self.lbl_cmd_info.setText(f"Cargando comandas (PIN {pin})...")
        self.table_cmd.setRowCount(0)

        def _fill(data: list):
            printers = discover_printers()
            self.lbl_cmd_info.setText(f"{len(data)} comandas | {len(printers)} impresoras detectadas")
            
            # Limpiar asignaciones de comandas que ya no existen
            current_comanda_ids = {str(com.get("id_comanda", "")) for com in data}
            assignments_to_remove = []
            for comanda_id in self.state.get("assignments_by_comanda", {}):
                if comanda_id not in current_comanda_ids:
                    assignments_to_remove.append(comanda_id)
            
            for comanda_id in assignments_to_remove:
                del self.state["assignments_by_comanda"][comanda_id]
                _log_debug(f"Auto-save: Asignación de comanda {comanda_id} eliminada (comanda ya no existe)")
            
            if assignments_to_remove:
                save_state(self.state)
            
            for com in data:
                row = self.table_cmd.rowCount()
                self.table_cmd.insertRow(row)

                id_com = str(com.get("id_comanda", ""))
                nom = str(com.get("nombre", ""))

                id_item = QTableWidgetItem(id_com)
                id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
                self.table_cmd.setItem(row, 0, id_item)

                nom_item = QTableWidgetItem(nom)
                nom_item.setFlags(nom_item.flags() & ~Qt.ItemIsEditable)
                self.table_cmd.setItem(row, 1, nom_item)

                combo = QComboBox()
                combo.addItem("— (sin asignar)")
                for p in printers:
                    combo.addItem(p)
                # Preselección si existe
                assigned = self.state.get("assignments_by_comanda", {}).get(id_com, "")
                if not assigned:
                    # Fallback: buscar en assignments
                    for p_name, com_data in self.state.get("assignments", {}).items():
                        if com_data and str(com_data.get("id_comanda", "")).strip() == str(id_com).strip():
                            assigned = p_name
                            break
                if assigned and assigned in printers:
                    combo.setCurrentText(assigned)
                # Conectar señal para guardado automático
                combo.currentTextChanged.connect(lambda text, cmd_id=id_com: self._auto_save_comanda_assignment(cmd_id, text))
                self.table_cmd.setCellWidget(row, 2, combo)


        self.cmd_tab_fetcher = ComandasFetcher(pin, get_base_url(self.state))
        t = threading.Thread(target=self.cmd_tab_fetcher.fetch, daemon=True)
        self.cmd_tab_fetcher.finished.connect(_fill)
        self.cmd_tab_fetcher.error.connect(lambda err: QMessageBox.critical(self, "Error", f"No se pudieron obtener las comandas:\n{err}"))
        t.start()

    def print_from_comanda_row(self, row: int):
        id_item = self.table_cmd.item(row, 0)
        if not id_item:
            return
        id_com = id_item.text()
        combo = self.table_cmd.cellWidget(row, 2)
        printer_name = None
        if isinstance(combo, QComboBox):
            txt = combo.currentText().strip()
            if txt and not txt.startswith("—"):
                printer_name = txt

        if not printer_name:
            QMessageBox.warning(self, "Sin impresora", "Selecciona una impresora para esta comanda.")
            return

        # La asignación ya se guarda automáticamente cuando cambia el dropdown

        # Pedimos id_operti
        last_id = self.state.get("last_id_operti", "")
        id_operti, ok = QInputDialog.getText(self, "ID Operti", "Ingresa id_operti del documento:", text=last_id)
        if not ok or not id_operti.strip():
            return
        id_operti = id_operti.strip()
        self.state["last_id_operti"] = id_operti
        save_state(self.state)

        url = build_ticket_url(self.state["pin"], id_com, id_operti, get_base_url(self.state))
        try:
            sumatra = _find_sumatra()
            print_pdf_from_url_auto(url, printer_name)
            usado = f" (Sumatra: {sumatra})" if sumatra else ""
            QMessageBox.information(self, "Impresión", f"Documento enviado a '{printer_name}'.{usado}")
        except Exception as e:
            extra = "\n\nÚltimas líneas del log (print_debug.log):\n" + _read_log_tail()
            QMessageBox.critical(self, "Error al imprimir", f"No se pudo imprimir:\n{e}{extra}")

    def delete_printer_row(self, row: int):
        name_item = self.table_printers.item(row, 0)
        if not name_item:
            return
        printer_name = name_item.text()

        reply = QMessageBox.question(
            self, "Eliminar impresora",
            f"¿Eliminar \"{printer_name}\" y su asignación?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        if printer_name in self.state["printers"]:
            self.state["printers"].remove(printer_name)
            _log_debug(f"Auto-save: Impresora '{printer_name}' eliminada de la lista")
        if printer_name in self.state["assignments"]:
            # Obtener el id_comanda antes de eliminarlo para limpiar de assignments_by_comanda
            comanda = self.state["assignments"].get(printer_name)
            if comanda and "id_comanda" in comanda:
                com_id = str(comanda["id_comanda"]).strip()
                if com_id in self.state.get("assignments_by_comanda", {}):
                    del self.state["assignments_by_comanda"][com_id]
                    _log_debug(f"Auto-save: Asignación de comanda {com_id} en assignments_by_comanda eliminada")
            del self.state["assignments"][printer_name]
            _log_debug(f"Auto-save: Asignación de impresora '{printer_name}' eliminada")
        save_state(self.state)

        self.table_printers.removeRow(row)

    def print_ticket_for_row(self, row: int):
        name_item = self.table_printers.item(row, 0)
        if not name_item:
            return
        printer_name = name_item.text()

        pin = self.state["pin"]
        com = self.state["assignments"].get(printer_name)
        comanda_id = com.get("id_comanda") if com else None

        # Pedimos id_operti si no hay uno reciente
        last_id = self.state.get("last_id_operti", "")
        id_operti, ok = QInputDialog.getText(self, "ID Operti", "Ingresa id_operti del documento:", text=last_id)
        if not ok or not id_operti.strip():
            return
        id_operti = id_operti.strip()
        self.state["last_id_operti"] = id_operti
        save_state(self.state)

        url = build_ticket_url(pin, comanda_id, id_operti, get_base_url(self.state))
        try:
            sumatra = _find_sumatra()
            print_pdf_from_url_auto(url, printer_name)
            usado = f" (Sumatra: {sumatra})" if sumatra else ""
            QMessageBox.information(self, "Impresión", f"Documento enviado a '{printer_name}'.{usado}")
        except Exception as e:
            sumatra = _find_sumatra()
            extra = "\n\nÚltimas líneas del log (print_debug.log):\n" + _read_log_tail()
            if not sumatra:
                extra += "\n\nSugerencia: coloca 'SumatraPDF.exe' en 'bin' o define SUMATRA_EXE.\n" + _sumatra_debug_info()
            QMessageBox.critical(self, "Error al imprimir", f"No se pudo imprimir:\n{e}{extra}")

    # ---------- Logs de Consola ----------
    def _append_log(self, text: str):
        if not hasattr(self, "txt_logs") or self.txt_logs is None:
            if not hasattr(self, "_log_buffer") or self._log_buffer is None:
                self._log_buffer = []
            self._log_buffer.append(text)
            return
            
        try:
            self.txt_logs.appendPlainText(text)
        except Exception:
            pass

    def _clear_logs(self):
        self.txt_logs.clear()

    def _copy_logs(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.txt_logs.toPlainText())
        QMessageBox.information(self, "Copiar", "Logs copiados al portapapeles.")

    def closeEvent(self, event):
        """Se llama cuando se cierra la ventana. Asegura que se detenga el servidor y cloudflared."""
        # Restaurar streams originales de consola
        if hasattr(self, "stdout_stream") and self.stdout_stream:
            sys.stdout = self.stdout_stream.original_stream
        if hasattr(self, "stderr_stream") and self.stderr_stream:
            sys.stderr = self.stderr_stream.original_stream

        # Detener el servidor y túnel si están activos
        if self.btn_server.isChecked():
            self.stop_server()
            self.btn_server.setChecked(False)
        else:
            self.stop_server()
        
        # Aceptar el evento de cierre
        event.accept()


# ==============================
# Entrypoint
# ==============================
def main():
    app = QApplication(sys.argv)

    # Bucle hasta que cloudflared esté disponible o el usuario elija continuar/salir
    while not _is_cloudflared_installed():
        opts_dlg = CloudflaredOptionsDialog()
        result = opts_dlg.exec()

        if result == QDialog.Rejected or opts_dlg.action == CloudflaredOptionsDialog.ACTION_EXIT:
            sys.exit(0)

        if opts_dlg.action == CloudflaredOptionsDialog.ACTION_MANUAL:
            manual_dlg = CloudflaredManualDialog()
            manual_dlg.exec()
            # Volver al diálogo de opciones para que el usuario elija qué hacer luego
            continue

        if opts_dlg.action == CloudflaredOptionsDialog.ACTION_AUTO_INSTALL:
            install_dlg = CloudflaredInstallDialog()
            install_dlg.exec()
            if _is_cloudflared_installed():
                break  # Instalación exitosa
            # Si falló, informar y volver a mostrar opciones (incluyendo manual)
            QMessageBox.warning(
                None,
                "Instalación incompleta",
                "La instalación automática no se completó.\n\n"
                "Posibles causas:\n"
                "• winget no está disponible en esta PC\n"
                "• Se requieren permisos de administrador\n"
                "• El instalador tardó demasiado\n\n"
                "Intenta la instalación manual desde el siguiente diálogo."
            )
            continue


    # Iniciar ventana principal
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
