from flask import Flask, send_file, abort
from threading import Thread
import time
import os

app = Flask(__name__)

# token -> {"path": str, "exp": float}
TEMP_LINKS = {}

@app.route("/")
def home():
    return "TB Loader is running ✅"

@app.route("/ping", methods=["GET", "HEAD"])
def ping():
    return "ok", 200

# ✅ download route
@app.route("/d/<token>")
def download_token(token):
    rec = TEMP_LINKS.get(token)
    if not rec:
        return abort(404)

    if time.time() > rec["exp"]:
        try:
            os.remove(rec["path"])
        except:
            pass
        TEMP_LINKS.pop(token, None)
        return abort(410)

    path = rec["path"]
    if not os.path.exists(path):
        TEMP_LINKS.pop(token, None)
        return abort(404)

    return send_file(path, as_attachment=True)

def keep_alive():
    def run():
        port = int(os.getenv("PORT", 10000))
        app.run(host="0.0.0.0", port=port)

    t = Thread(target=run)
    t.daemon = True
    t.start()
