import RPi.GPIO as GPIO
import os
import time

BUTTON_PIN = 3  # GPIO3 (pin físico 5)

GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN, GPIO.IN)

print("Esperando que presiones el botón...")

try:
    while True:
        if GPIO.input(BUTTON_PIN) == GPIO.LOW:
            print("Botón presionado. Apagando...")
            time.sleep(0.2)  # Debounce
            os.system("sudo shutdown -h now")
            break
        time.sleep(0.1)
except KeyboardInterrupt:
    GPIO.cleanup()
