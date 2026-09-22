import RPi.GPIO as GPIO
import time

# Pines
TRIG = 23
ECHO = 24
MOTOR_PINS = [17, 27, 22]  # Motores vibradores en GPIO 17, 27 y 22

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)
for pin in MOTOR_PINS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)

def get_distance():
    GPIO.output(TRIG, False)
    time.sleep(0.002)

    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    start_time = time.time()
    timeout = start_time + 0.04  # 40 ms

    # Pulso de subida
    while GPIO.input(ECHO) == 0 and time.time() < timeout:
        start_time = time.time()

    end_time = time.time()
    # Pulso de bajada
    while GPIO.input(ECHO) == 1 and time.time() < timeout:
        end_time = time.time()

    pulse_duration = end_time - start_time
    distance = round(pulse_duration * 34300 / 2, 2)  # cm
    return distance

def calcular_pausa_y_duracion(dist):
    # Fuera de rango útil
    if dist <= 0 or dist > 200:
        return None, None

    # Tramos: 60 (lo más cerca) + 2 rangos más hasta 200
    if dist <= 60:
        return 0.03, 0.05     # muy cerca (rápido)
    elif dist <= 120:
        return 0.07, 0.05     # cerca
    else:  # 120–200
        return 0.20, 0.07     # medio/lejos

try:
    print("Sistema iniciado: 60 cm = 'lo más cerca', con 2 rangos más hasta 200 cm")
    while True:
        dist = get_distance()
        pausa, duracion = calcular_pausa_y_duracion(dist)

        if pausa is None:
            print("Fuera de rango (>200 cm)")
            time.sleep(0.5)
        else:
            print(f"Distancia: {dist:.2f} cm -> pausa: {pausa*1000:.0f} ms, duración: {duracion*1000:.0f} ms")

            # Encender todos los motores
            for pin in MOTOR_PINS:
                GPIO.output(pin, GPIO.HIGH)
            time.sleep(duracion)

            # Apagar todos los motores
            for pin in MOTOR_PINS:
                GPIO.output(pin, GPIO.LOW)
            time.sleep(pausa)

except KeyboardInterrupt:
    print("Programa detenido por el usuario")
finally:
    for pin in MOTOR_PINS:
        GPIO.output(pin, GPIO.LOW)
    GPIO.cleanup()
