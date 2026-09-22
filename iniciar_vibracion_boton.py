#!/usr/bin/env python3
import os
import time
import signal
import subprocess
import RPi.GPIO as GPIO
from pathlib import Path

# --- CONFIGURACIÓN ---
BUTTON_PIN = 26  # GPIO26
MOTOR_PINS = [17, 27, 22]  # Pines de vibración

# REVISA QUE ESTE NOMBRE SEA EL CORRECTO.
# En tu mensaje anterior decía "codigo_proximidad.py", pero antes hicimos "proximidad_final_v2.py"
# Asegúrate de que apunte al archivo que realmente funciona.
SCRIPT = "/home/jorgel24/Desktop/codigo_proximidad.py"
WORKDIR = str(Path(SCRIPT).parent)

# --- FUNCIONES VISUALES (GUI) ---
def env_for_gui():
    """Configura la pantalla para que la ventana se vea"""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XAUTHORITY", "/home/jorgel24/.Xauthority")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    return env

def term_cmd(cmd, title="Sistema Vibracion"):
    # -bg black (Fondo negro)
    # -fg white (Texto blanco)
    return ["xterm", "-title", title, "-fg", "white", "-bg", "black", "-e", "bash", "-lc", cmd]

# --- FUNCIONES DE SEGURIDAD (MOTORES) ---
def forzar_apagado_motores():
    """Apaga los motores físicamente para evitar que se queden pegados"""
    # print("Forzando apagado de motores...")
    GPIO.setmode(GPIO.BCM)
    for pin in MOTOR_PINS:
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)

# --- INICIO ---
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Seguridad inicial
forzar_apagado_motores()

process = None
last_state = GPIO.input(BUTTON_PIN)
CMD_STR = f"/usr/bin/python3 {SCRIPT}"

print(f"Boton Vibracion Listo. Controlando: {SCRIPT}")

try:
    while True:
        current_state = GPIO.input(BUTTON_PIN)
        
        # Detectar presión (Flanco de bajada)
        if last_state == GPIO.HIGH and current_state == GPIO.LOW:
            time.sleep(0.05) # Debounce
            
            # Confirmar que sigue presionado
            if GPIO.input(BUTTON_PIN) == GPIO.LOW:
                
                if process is None or process.poll() is not None:
                    # --- ENCENDER (ABRIR VENTANA) ---
                    print(">>> Abriendo ventana de Proximidad...")
                    process = subprocess.Popen(
                        term_cmd(CMD_STR),       # Comando xterm
                        cwd=WORKDIR,             # Carpeta correcta
                        env=env_for_gui(),       # Variables de pantalla
                        preexec_fn=os.setsid     # Grupo de procesos nuevo
                    )
                else:
                    # --- APAGAR (CERRAR VENTANA Y MOTORES) ---
                    print(">>> Cerrando ventana...")
                    
                    # 1. Matar xterm y sus hijos
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except Exception as e:
                        print(f"Error al cerrar: {e}")
                    
                    process = None
                    
                    # 2. SEGURIDAD: APAGAR MOTORES
                    forzar_apagado_motores()
                
            time.sleep(0.5) # Espera para no rebotar
            
        last_state = current_state
        time.sleep(0.05)

except KeyboardInterrupt:
    pass
finally:
    if process:
        try: os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except: pass
    forzar_apagado_motores()
    GPIO.cleanup()
