#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import re
import sys
import time
import signal
import threading
import os
import json
import shutil
from pathlib import Path
import struct
import wave
import contextlib

# Evita errores de codificación en terminales latin-1
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# =========================================================
# CONFIGURACIÓN
# =========================================================
POST_PROCESS_FILE = "/home/jorgel24/Desktop/AI/rpi-camera-assets/imx500_mobilenet_ssd.json"
BLUETOOTH_SINK    = "bluez_output.41_42_D1_AE_8E_D4.1"

# Piper
PIPER_BIN = os.path.expanduser("~/.local/bin/piper")
if not os.path.isfile(PIPER_BIN):
    PIPER_BIN = "piper"

VOICE_DIR    = "/opt/piper-voices/es_MX-ald-medium"
PIPER_MODEL  = os.path.join(VOICE_DIR, "es_MX-ald-medium.onnx")
PIPER_CONFIG = os.path.join(VOICE_DIR, "es_MX-ald-medium.onnx.json")

# Prosodia
SENTENCE_SILENCE = 0.00
LENGTH_SCALE     = 0.95

# Cámara
CAM_CMD = [
    "rpicam-hello",
    "-t", "0",
    "--post-process-file", POST_PROCESS_FILE,
    "--viewfinder-width", "1920",
    "--viewfinder-height", "1080",
    "--framerate", "30",
    "--verbose", "2",
]

# Impresiones
PRINT_INTERVAL = 6.0
MAX_PRINTS_PER_INTERVAL = 2   # <= solo 2 detecciones por intervalo

# Repetición si no cambia estado (voz)
MIN_ALERT_INTERVAL = 6.0

# Audio/BT
PW_LATENCY_FRAMES = 1536   # PipeWire (~69 ms a 22050 Hz)
PACAT_LATENCY_MSEC = 60    # fallback si no hay pw-cat
BT_WARMUP_MS       = 200   # calienta BT

# Caché en RAM
CACHE_DIR = Path("/dev/shm/piper_cache") if Path("/dev/shm").exists() else Path.home() / ".cache/piper_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ====== ZONAS con HISTÉRESIS espacial ======
FRAME_WIDTH   = 1920
ZONE_LC       = FRAME_WIDTH // 3          # ~640
ZONE_CR       = (FRAME_WIDTH * 2) // 3    # ~1280
ZONE_MARGIN   = 100                       # banda muerta ±100 px

# ====== DISTANCIA con HISTÉRESIS de área ======
AREA_NEAR_BASE     = 300_000
AREA_HYSTERESIS    = int(AREA_NEAR_BASE * 0.20)     # 20%
AREA_NEAR_ENTER    = AREA_NEAR_BASE + AREA_HYSTERESIS
AREA_FAR_ENTER     = AREA_NEAR_BASE - AREA_HYSTERESIS

# ====== SUAVIZADO de cambio ======
PREEMPT_PAUSE_MS   = 80    # pausa tras cortar audio
PREROLL_SIL_MS     = 60    # silencio incluido EN EL MISMO WAV

# ====== ESTABILIDAD/DEBOUNCE de cambio ======
CHANGE_STABLE_MS     = 250  # debe mantenerse así este tiempo
CHANGE_DEBOUNCE_SEC  = 1.0  # mínimo entre dos “inmediatos”

# ====== CONGELAR REPETICIONES mientras hay cambio ======
FREEZE_REPEATS_ON_CHANGE_SEC = 0.8

# =========================================================
# CLASES: filtro y traducción (inglés -> español)
# =========================================================
CLASS_MAP = {
    "person": "Persona",
    "bicycle": "Bicicleta",
    "car": "Coche",
    "motorcycle": "Motocicleta",
    "bus": "Autobus",
    "truck": "Camion",
    "traffic light": "Semaforo",
    "stop sign": "Señal de stop",
    "bench": "Banca",
    "dog": "Perro",
    "cat": "Gato",
    "backpack": "Mochila",
    "umbrella": "Paraguas",
    "suitcase": "Maleta",
    "skateboard": "Patineta",
    "sports ball": "Pelota deportiva",
    "bottle": "Botella",
    "cup": "Taza",
    "book": "Libro",
    "chair": "Silla",
    "couch": "Sofa",
    "potted plant": "Planta en maceta",
    "bed": "Cama",
    "dining table": "Mesa de comedor",
    "toilet": "Inodoro",
    "tv": "Televisor",
    "laptop": "Portatil",
    "mouse": "Raton",
    "remote": "Mando a distancia",
    "keyboard": "Teclado",
    "cell phone": "Telefono movil",
    "microwave": "Microondas",
    "oven": "Horno",
    "toaster": "Tostadora",
    "sink": "Fregadero",
    "refrigerator": "Nevera",
    "clock": "Reloj",
    "vase": "Florero",
    "scissors": "Tijeras",
    "hair drier": "Secador de pelo",
    "toothbrush": "Cepillo de dientes",
}
ALLOWED_CLASSES = set(CLASS_MAP.keys())

# =========================================================
# UTILIDADES
# =========================================================
def read_sample_rate(cfg_path: str, fallback: int = 22050) -> int:
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return int(json.load(f)["audio"]["sample_rate"])
    except Exception:
        return fallback

def warmup_bluetooth(rate_hz: int, sink: str, ms: int = 200):
    frames = int(rate_hz * ms / 1000)
    zeros = b"\x00\x00" * frames  # S16_LE mono
    pwcat = shutil.which("pw-cat")
    pacat = shutil.which("pacat")
    paplay = shutil.which("paplay") or "paplay"
    try:
        if pwcat:
            proc = subprocess.Popen(
                [pwcat, "--playback", f"--target={sink}",
                 f"--latency={PW_LATENCY_FRAMES}", "--format=s16", f"--rate={rate_hz}", "--channels=1"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        elif pacat:
            proc = subprocess.Popen(
                [pacat, "--playback", f"--device={sink}", "--format=s16le",
                 f"--rate={rate_hz}", "--channels=1", f"--latency-msec=20"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            proc = subprocess.Popen(
                [paplay, "--raw", f"--rate={rate_hz}", "--format=s16le",
                 "--channels=1", f"--device={sink}"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        proc.stdin.write(zeros); proc.stdin.close()
        proc.wait(timeout=1.0)
    except Exception:
        pass

def make_silence_pcm(rate: int, ms: int) -> bytes:
    frames = int(rate * ms / 1000)
    return b"\x00\x00" * frames  # S16LE mono

def wav_to_pcm(path: Path):
    with contextlib.closing(wave.open(str(path), "rb")) as wf:
        rate = wf.getframerate()
        ch   = wf.getnchannels()
        samp = wf.getsampwidth()
        data = wf.readframes(wf.getnframes())
    return data, rate, ch if ch else 1

def write_pcm_as_wav(pcm: bytes, rate: int, out_path: Path):
    with contextlib.closing(wave.open(str(out_path), "wb")) as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)

def make_immediate_wav(base_wav: Path, out_wav: Path, preroll_ms: int, rate: int):
    """Crea un WAV nuevo que contiene [silencio preroll + frase] en UN SOLO archivo."""
    if out_wav.exists():
        return
    pcm, r, ch = wav_to_pcm(base_wav)
    if r != rate:
        write_pcm_as_wav(pcm, r, out_wav)  # sin preroll si rate no coincide
        return
    preroll = make_silence_pcm(rate, preroll_ms) if preroll_ms > 0 else b""
    write_pcm_as_wav(preroll + pcm, rate, out_wav)

# =========================================================
# TTS con caché + cola + preempción (por clase)
# =========================================================
import re as _re

def _slug(s: str) -> str:
    s = s.lower().replace(" ", "_")
    return _re.sub(r"[^a-z0-9_áéíóúñü\-]", "", s)

class SmartCachedTTS:
    def __init__(self, piper_bin, model, config, rate_hz, sink,
                 length_scale=None, sentence_silence=None):
        self.piper_bin = piper_bin
        self.model = model
        self.config = config
        self.rate = int(rate_hz)
        self.sink = sink
        self.length_scale = length_scale
        self.sentence_silence = sentence_silence

        self._env = os.environ.copy()
        self._env["PULSE_SINK"] = self.sink

        # Reproductores
        self._pwcat  = shutil.which("pw-cat")
        self._pacat  = shutil.which("pacat")
        self._paplay = shutil.which("paplay") or "paplay"

        # Cachés
        self.cache_normal    = {}  # (class_en, dist, pos) -> WAV normal
        self.cache_immediate = {}  # (class_en, dist, pos) -> WAV con prerroll

        # Cola coalescente global
        self._cv = threading.Condition()
        self._pending_key = None
        self._stopping = False

        # Estado de reproductor
        self._player_lock = threading.Lock()
        self._player_proc = None

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)

    def _piper_to_wav(self, text: str, out_wav: Path):
        args = [
            self.piper_bin, "--model", self.model, "--config", self.config,
            "--length-scale", str(self.length_scale) if self.length_scale is not None else "1.0",
            "-f", str(out_wav)
        ]
        if self.sentence_silence is not None:
            args += ["--sentence-silence", str(self.sentence_silence)]
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, env=self._env)
        p.stdin.write((text.strip() + "\n").encode("utf-8"))
        p.stdin.close()
        rc = p.wait()
        if rc != 0:
            err = p.stderr.read().decode("utf-8", "ignore")
            raise RuntimeError(f"Piper falló ({rc}): {err}")

    def _ensure_base_wav(self, class_en: str, label_es: str, dist: str, pos: str) -> Path:
        key = (class_en, dist, pos)
        if key in self.cache_normal and self.cache_normal[key].exists():
            return self.cache_normal[key]
        slug = _slug(label_es)
        base_wav = CACHE_DIR / f"{slug}_{dist}_{pos.replace(' ', '_')}_{class_en}.wav"
        if not base_wav.exists():
            self._piper_to_wav(f"{label_es} {dist} {pos}.", base_wav)
        self.cache_normal[key] = base_wav
        return base_wav

    def _ensure_imm_wav(self, class_en: str, label_es: str, dist: str, pos: str) -> Path:
        key = (class_en, dist, pos)
        if key in self.cache_immediate and self.cache_immediate[key].exists():
            return self.cache_immediate[key]
        base = self._ensure_base_wav(class_en, label_es, dist, pos)
        imm_wav = Path(str(base).replace(".wav", "_imm.wav"))
        make_immediate_wav(base, imm_wav, PREROLL_SIL_MS, self.rate)
        self.cache_immediate[key] = imm_wav
        return imm_wav

    def prepare_cache(self):
        # Para arranque rápido, solo precalienta "persona"
        combos = [
            ("cerca", "a la izquierda"), ("cerca", "al centro"), ("cerca", "a la derecha"),
            ("lejos", "a la izquierda"), ("lejos", "al centro"), ("lejos", "a la derecha"),
        ]
        for dist, pos in combos:
            self._ensure_base_wav("person", "Persona", dist, pos)
            self._ensure_imm_wav("person", "Persona", dist, pos)

    def _play_blocking(self, wav_path: Path):
        if self._pwcat:
            cmd = [
                self._pwcat, "--playback", f"--target={self.sink}",
                f"--latency={PW_LATENCY_FRAMES}", str(wav_path)
            ]
        elif self._pacat:
            cmd = [
                self._pacat, "--playback", f"--device={self.sink}",
                "--file-format=wav", f"--latency-msec={PACAT_LATENCY_MSEC}", str(wav_path)
            ]
        else:
            cmd = [self._paplay, f"--device={self.sink}", str(wav_path)]

        with self._player_lock:
            self._player_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self._env
            )
        try:
            self._player_proc.wait()
        finally:
            with self._player_lock:
                self._player_proc = None

    def _terminate_player(self):
        with self._player_lock:
            if self._player_proc and (self._player_proc.poll() is None):
                try:
                    self._player_proc.terminate()
                    try:
                        self._player_proc.wait(timeout=0.3)
                    except subprocess.TimeoutExpired:
                        self._player_proc.kill()
                except Exception:
                    pass
                finally:
                    self._player_proc = None

    def start(self):
        self._worker.start()

    def stop(self):
        with self._cv:
            self._stopping = True
            self._pending_key = None
            self._cv.notify_all()
        self._worker.join(timeout=1.5)
        self._terminate_player()

    # Repite (cada N s) si estado es igual (no solapa)
    def speak_queued(self, class_en: str, label_es: str, dist_phrase: str, pos_phrase: str):
        key = (class_en, dist_phrase, pos_phrase)
        wav = self.cache_normal.get(key)
        if not wav or not wav.exists():
            wav = self._ensure_base_wav(class_en, label_es, dist_phrase, pos_phrase)
        with self._cv:
            self._pending_key = key  # coalesce: solo la última encolada
            self._cv.notify()

    # Cambio de estado: inmediato con prerroll (en un solo archivo)
    def speak_immediate(self, class_en: str, label_es: str, dist_phrase: str, pos_phrase: str):
        key = (class_en, dist_phrase, pos_phrase)
        wav = self.cache_immediate.get(key)
        if not wav or not wav.exists():
            wav = self._ensure_imm_wav(class_en, label_es, dist_phrase, pos_phrase)
        with self._cv:
            self._pending_key = None  # limpia cualquier pendiente
        self._terminate_player()
        if PREEMPT_PAUSE_MS > 0:
            time.sleep(PREEMPT_PAUSE_MS / 1000.0)
        threading.Thread(target=self._play_blocking, args=(wav,), daemon=True).start()

    def _worker_loop(self):
        while True:
            with self._cv:
                while (self._pending_key is None) and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    break
                key = self._pending_key
                self._pending_key = None
            wav = self.cache_normal.get(key)
            if wav and wav.exists():
                self._play_blocking(wav)

# =========================================================
# CLASIFICADORES con HISTÉRESIS
# =========================================================
def classify_zone(cx: int, last_zone: str | None) -> tuple[str, str]:
    if cx <= ZONE_LC - ZONE_MARGIN:
        return "izquierda", "a la izquierda"
    if cx >= ZONE_CR + ZONE_MARGIN:
        return "derecha", "a la derecha"
    if (ZONE_LC + ZONE_MARGIN) <= cx <= (ZONE_CR - ZONE_MARGIN):
        return "centro", "al centro"
    if last_zone in ("izquierda", "centro", "derecha"):
        mapping = {"izquierda":"a la izquierda","centro":"al centro","derecha":"a la derecha"}
        return last_zone, mapping[last_zone]
    if cx < ZONE_LC:
        return "izquierda", "a la izquierda"
    if cx > ZONE_CR:
        return "derecha", "a la derecha"
    return "centro", "al centro"

def classify_distance(area: int, last_dist: str | None) -> tuple[str, str]:
    if last_dist is None:
        return ("cerca", "cerca") if area > AREA_NEAR_BASE else ("lejos", "lejos")
    if last_dist == "cerca":
        if area < AREA_FAR_ENTER: return "lejos", "lejos"
        return "cerca", "cerca"
    if area > AREA_NEAR_ENTER:   return "cerca", "cerca"
    return "lejos", "lejos"

# =========================================================
# INICIO
# =========================================================
PIPER_RATE = read_sample_rate(PIPER_CONFIG, fallback=22050)

tts = SmartCachedTTS(PIPER_BIN, PIPER_MODEL, PIPER_CONFIG, PIPER_RATE, BLUETOOTH_SINK,
                     LENGTH_SCALE, SENTENCE_SILENCE)

warmup_bluetooth(PIPER_RATE, BLUETOOTH_SINK, BT_WARMUP_MS)

try:
    print("[TTS] Preparando cache de frases...")
    tts.prepare_cache()
    tts.start()
    print("[TTS] Cache lista y reproductor iniciado.")
except Exception as e:
    print(f"[TTS] Error preparando cache: {e}", file=sys.stderr)

process = subprocess.Popen(
    CAM_CMD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, start_new_session=True
)

print("Capturando detecciones...\n"); sys.stdout.flush()

pattern = re.compile(
    r"\[\d+\]\s*:\s*([^\[]+)\[\d+\]\s+\(([\d.]+)\)\s+@\s+(\d+),(\d+)\s+(\d+)x(\d+)"
)

detections_lock = threading.Lock()
second_detections = {}
running = True

def clean_exit():
    global running
    running = False
    try: tts.stop()
    except Exception: pass
    if process and (process.poll() is None):
        try:
            process.terminate(); process.wait(timeout=1.0)
        except Exception:
            try: process.kill()
            except Exception: pass
    print("\n[SALIDA] Limpiado. Bye."); sys.exit(0)

signal.signal(signal.SIGINT,  lambda *_: clean_exit())
signal.signal(signal.SIGTERM, lambda *_: clean_exit())

def printer_thread(interval=PRINT_INTERVAL):
    global second_detections
    while running:
        time.sleep(interval)
        with detections_lock:
            items = list(second_detections.items())
            # Limita a 3 por intervalo y descarta el resto
            items = items[:MAX_PRINTS_PER_INTERVAL]
            second_detections.clear()
        if not items:
            continue
        lines = ["\n*** Detecciones del intervalo ***"]
        for obj_es, (pos, dist) in items:
            lines += [f"Objeto: {obj_es}", f"Posicion: {pos}", f"Distancia: {dist}", "-"*30]
        print("\n".join(lines)); sys.stdout.flush()

threading.Thread(target=printer_thread, args=(PRINT_INTERVAL,), daemon=True).start()

# ===== Estados por clase =====
last_zone_id_by_class  = {}      # class_en -> zone_id
last_dist_id_by_class  = {}      # class_en -> dist_id
last_state_by_class    = {}      # class_en -> (dist_phrase, pos_phrase)
last_speak_ts_by_class = {}      # class_en -> ts
pending_state_by_class = {}      # class_en -> (dist,pos)
pending_since_ts_by_class = {}   # class_en -> ts
last_change_ts_by_class = {}     # class_en -> ts
freeze_repeats_until_by_class = {}  # class_en -> ts

# =========================================================
# BUCLE PRINCIPAL
# =========================================================
try:
    while running:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                print("[CAM] rpicam-hello termino. Saliendo..."); clean_exit()
            continue
        line = line.strip()
        if not line.startswith('['): continue

        match = pattern.search(line)
        if not match: continue

        # Nombre de clase EXACTO en inglés (del log)
        obj_en = match.group(1).strip().lower()
        if obj_en not in ALLOWED_CLASSES:
            continue
        obj_es = CLASS_MAP[obj_en]

        x, y, w, h = map(int, match.group(3, 4, 5, 6))
        cx = x + w // 2
        area = w * h

        # Histéresis por clase
        last_zone_id = last_zone_id_by_class.get(obj_en)
        last_dist_id = last_dist_id_by_class.get(obj_en)

        zone_id, pos_phrase  = classify_zone(cx, last_zone_id)
        dist_id, dist_phrase = classify_distance(area, last_dist_id)

        # Impresión agrupada (español) — limita a 3 en el buffer
        pos_text  = {"izquierda":"A la izquierda","centro":"Al centro","derecha":"A la derecha"}[zone_id]
        dist_text = "Cerca" if dist_id == "cerca" else "Lejos"
        with detections_lock:
            if (obj_es not in second_detections) and (len(second_detections) < MAX_PRINTS_PER_INTERVAL):
                second_detections[obj_es] = (pos_text, dist_text)

        # ===== Voz para TODAS las clases permitidas =====
        now = time.time()
        state = (dist_phrase, pos_phrase)

        last_state    = last_state_by_class.get(obj_en)
        last_speak_ts = last_speak_ts_by_class.get(obj_en, 0.0)
        pending_state = pending_state_by_class.get(obj_en)
        pending_since = pending_since_ts_by_class.get(obj_en, 0.0)
        last_change   = last_change_ts_by_class.get(obj_en, 0.0)
        freeze_until  = freeze_repeats_until_by_class.get(obj_en, 0.0)

        if last_state is None:
            tts.speak_immediate(obj_en, obj_es, dist_phrase, pos_phrase)
            last_state_by_class[obj_en] = state
            last_speak_ts_by_class[obj_en] = now
            last_change_ts_by_class[obj_en] = now
            pending_state_by_class[obj_en] = None
            freeze_repeats_until_by_class[obj_en] = now

        elif state != last_state:
            if pending_state != state:
                pending_state_by_class[obj_en] = state
                pending_since_ts_by_class[obj_en] = now
                freeze_repeats_until_by_class[obj_en] = now + FREEZE_REPEATS_ON_CHANGE_SEC

            stable = (now - pending_since_ts_by_class.get(obj_en, now)) >= (CHANGE_STABLE_MS / 1000.0)
            enough_gap = (now - last_change) >= CHANGE_DEBOUNCE_SEC
            if stable and enough_gap:
                tts.speak_immediate(obj_en, obj_es, dist_phrase, pos_phrase)
                last_state_by_class[obj_en] = state
                last_speak_ts_by_class[obj_en] = now
                last_change_ts_by_class[obj_en] = now
                pending_state_by_class[obj_en] = None
                freeze_repeats_until_by_class[obj_en] = now + 0.4

        else:
            pending_state_by_class[obj_en] = None
            if (now >= freeze_until) and ((now - last_speak_ts) >= MIN_ALERT_INTERVAL):
                tts.speak_queued(obj_en, obj_es, dist_phrase, pos_phrase)
                last_speak_ts_by_class[obj_en] = now

        # Actualiza histéresis por clase
        last_zone_id_by_class[obj_en] = zone_id
        last_dist_id_by_class[obj_en] = dist_id

except KeyboardInterrupt:
    clean_exit()
except Exception as e:
    print("Error:", e); clean_exit()
