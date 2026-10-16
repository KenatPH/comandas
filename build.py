#!/usr/bin/env python3
"""
Script simple para crear ejecutable de Comandar Server
"""
import os
import sys
import subprocess
import shutil

# Forzar codificación UTF-8 en stdout y stderr (evita UnicodeEncodeError en Windows con emojis)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def main():
    print("🚀 Creando ejecutable Comandar Server...")
    
    # Verificar archivos necesarios
    if not os.path.exists("app.py"):
        print("❌ Error: app.py no encontrado")
        return
    
    # Instalar dependencias necesarias
    print("📦 Instalando dependencias...")
    try:
        import PySide6
        print("✅ PySide6 encontrado")
    except ImportError:
        print("📦 Instalando PySide6...")
        subprocess.run([sys.executable, "-m", "pip", "install", "PySide6"], check=True)

    try:
        import requests
        print("✅ requests encontrado")
    except ImportError:
        print("📦 Instalando requests...")
        subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)

    try:
        import PyInstaller
        print("✅ PyInstaller encontrado")
    except ImportError:
        print("📦 Instalando PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller", "pillow"], check=True)
    
    # Configurar icono
    icon_arg = []
    if os.path.exists("app_icon.ico"):
        print("🎨 Usando icono existente...")
        icon_arg = ["--icon", "app_icon.ico"]
        print("✅ Icono configurado")
    elif os.path.exists("app_icon.jpg"):
        print("🎨 Convirtiendo icono desde JPG...")
        try:
            from PIL import Image
            img = Image.open("app_icon.jpg")
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            # Crear ICO con múltiples tamaños
            sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
            images = []
            for size in sizes:
                resized = img.resize(size, Image.Resampling.LANCZOS)
                images.append(resized)
            
            images[0].save("app_icon.ico", format='ICO', sizes=[(img.width, img.height) for img in images])
            icon_arg = ["--icon", "app_icon.ico"]
            print("✅ Icono convertido y configurado")
        except Exception as e:
            print(f"⚠️  Error con icono: {e}")
    else:
        print("⚠️  No se encontró archivo de icono (app_icon.ico o app_icon.jpg)")
    
    # Comando PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name=ComandarServer",
        "--clean",
        "--noconfirm",
        "--hidden-import=PySide6.QtCore",
        "--hidden-import=PySide6.QtWidgets",
        "--hidden-import=PySide6.QtGui",
        "--collect-all=requests",
        "--hidden-import=urllib3",
        "--hidden-import=certifi",
        "--hidden-import=charset_normalizer",
        "--hidden-import=idna",
        "--hidden-import=win32api",
        "--hidden-import=win32print",
        "--hidden-import=win32con",
        f"--add-data=app_state.json{os.pathsep}.",
        f"--add-data=comandar_server/bin{os.pathsep}bin"
    ] + icon_arg + ["app.py"]
    
    print(f"🔨 Comando PyInstaller: {' '.join(cmd)}")
    if icon_arg:
        print(f"🎨 Usando icono: {icon_arg[1]}")
    else:
        print("⚠️  No se usará icono personalizado")
    
    print("🔨 Creando ejecutable...")
    try:
        subprocess.run(cmd, check=True)
        print("✅ ¡Ejecutable creado!")
        if os.path.exists("dist/ComandarServer") and not os.path.exists("dist/ComandarServer.exe"):
            shutil.copy2("dist/ComandarServer", "dist/ComandarServer.exe")
        print("📁 Ubicación: dist/ComandarServer.exe")
        
        # Copiar archivos adicionales
        files_to_copy = [
            "app_state.json",
            "app_icon.ico",
            "app_icon.jpg"
        ]
        
        for file in files_to_copy:
            if os.path.exists(file):
                shutil.copy2(file, "dist/")
                print(f"📋 Copiado: {file}")
        
        # Copiar directorio bin si existe
        if os.path.exists("comandar_server/bin"):
            if os.path.exists("dist/bin"):
                shutil.rmtree("dist/bin")
            shutil.copytree("comandar_server/bin", "dist/bin")
            print("📁 Copiado directorio: comandar_server/bin → dist/bin")
        
        # Verificar que SumatraPDF esté disponible
        sumatra_paths = [
            "dist/bin/SumatraPDF.exe",
            "dist/bin/SumatraPDF-3.5.2-64.exe",
            "dist/SumatraPDF.exe",
            "dist/SumatraPDF-3.5.2-64.exe"
        ]
        
        sumatra_found = False
        for path in sumatra_paths:
            if os.path.exists(path):
                print(f"✅ SumatraPDF encontrado: {path}")
                sumatra_found = True
                break
        
        if not sumatra_found:
            print("⚠️  ADVERTENCIA: SumatraPDF no encontrado en dist/")
            print("   Asegúrate de que esté en comandar_server/bin/")
        
        # Crear archivo de configuración inicial si no existe
        if not os.path.exists("dist/app_state.json"):
            import json
            default_config = {
                "pin": "41038",
                "printers": [],
                "assignments": {},
                "assignments_by_comanda": {},
                "last_id_operti": "",
                "server": {
                    "host": "0.0.0.0",
                    "port": 8443,
                    "tls_cert": "",
                    "tls_key": ""
                }
            }
            with open("dist/app_state.json", "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            print("📋 Creado archivo de configuración inicial")
        
        print("\n🎉 ¡Build completado exitosamente!")
        print("📁 Archivos en dist/:")
        for item in os.listdir("dist"):
            print(f"   - {item}")
        
    except subprocess.CalledProcessError:
        print("❌ Error creando ejecutable")

if __name__ == "__main__":
    main()
