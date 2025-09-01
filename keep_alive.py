# keep_alive.py

from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "I am alive!", 200  # Render can use this as basic health check

@app.route('/ping')
def ping():
    return "pong", 200  # You can use this with UptimeRobot or any external ping tool

def run():
    print("[*] Starting Flask keep-alive server...")
    port = int(os.environ.get("PORT", 8080))  # Render assigns PORT
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
