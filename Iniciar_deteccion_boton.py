#!/usr/bin/env python3
import os, time, signal, subprocess
import RPi.GPIO as GPIO
from pathlib import Path

# --- CONFIGURACIÓN ---
PIN = 6  # GPIO6 (BCM)
SCRIPT = "/home/jorgel24/Desktop/codigo_detecciones.py"
LOG_FILE = "/home/jorgel24/Desktop/registro_baston.log" # Aquí se guardará lo que diga el programa
WORKDIR = str(Path(SCRIPT).parent)
MIN_COOLDOWN = 1.2 

last_toggle = 0.0
pressed = False
process = None
log_handle = None

# --- FUNCIONES ---

def start_detection():
    global process, log_handle
    print(">>> INICIANDO SISTEMA DE DETECCIÓN (MODO SILENCIOSO)...")
    
    # Abrimos el archivo de log para guardar los prints
    log_handle = open(LOG_FILE, "a") 
    
    # Ejecutamos Python directamente, SIN xterm (Ahorra mucha CPU)
    process = subprocess.Popen(
        ["/usr/bin/python3", "-u", SCRIPT], # -u hace que los prints salgan al instante
        cwd=WORKDIR,
        stdout=log_handle, # Redirigir salida al archivo
        stderr=log_handle, # Redirigir errores al archivo
        preexec_fn=os.setsid # Crear grupo de procesos para poder matarlos todos juntos
    )
    print(f">>> Proceso iniciado con PID: {process.pid}")

def stop_detection():
    global process, log_handle
    if process:
        print(">>> DETENIENDO SISTEMA...")
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGTERM) # Matar a todo el grupo (script + subprocesos)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL) # Si no muere, forzar muerte
        except Exception as e:
            print(f"Error al detener: {e}")
        
        process = None
    
    # Cerrar el archivo de log
    if log_handle:
        log_handle.close()
        log_handle = None
    
    # Limpieza extra de seguridad
    subprocess.run(["pkill", "-f", "rpicam"], check=False)
    subprocess.run(["pkill", "-f", "libcamera"], check=False)
    print(">>> SISTEMA DETENIDO.")

def wait(ms):
    time.sleep(ms / 1000.0)

# --- SETUP GPIO ---
GPIO.setmode(GPIO.BCM)
GPIO.setup(PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

print(f"LISTO. Presiona el botón en GPIO {PIN} para activar/desactivar.")
print(f"Los logs se guardarán en: {LOG_FILE}")

# --- BUCLE PRINCIPAL ---
try:
    while True:
        val = GPIO.input(PIN)

        # DETECTAR PULSACIÓN (Low = presionado por PULL_UP)
        if not pressed and val == GPIO.LOW:
            wait(40) # Debounce
            if GPIO.input(PIN) == GPIO.LOW:
                pressed = True

        # DETECTAR SOLTADO (High = soltado)
        elif pressed and val == GPIO.HIGH:
            wait(40)
            if GPIO.input(PIN) == GPIO.HIGH:
                now = time.monotonic()
                if now - last_toggle >= MIN_COOLDOWN:
                    
                    # LOGICA DE TOGGLE
                    if process is None or process.poll() is not None:
                        start_detection()
                    else:
                        stop_detection()
                        
                    last_toggle = now
                pressed = False

        wait(50) # Ahorro de CPU en el bucle del botón

except KeyboardInterrupt:
    print("\nSaliendo...")
finally:
    stop_detection()
    GPIO.cleanup()
