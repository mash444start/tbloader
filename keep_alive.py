from flask import Flask, send_file, abort
import time
import os

app = Flask(__name__)

# token -> {"path": str, "exp": float}
TEMP_LINKS = {}

@app.route("/")
def home():
    return "TB Loader is running ✅"

@app.route("/ping", methods=["HEAD", "GET"])
def ping():
    return "ok", 200

# ✅ Temporary download link route (valid 5 minutes)
@app.route("/d/<token>")
def download_token(token):
    rec = TEMP_LINKS.get(token)
    if not rec:
        return abort(404)

    # expired
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
    from threading import Thread
    def run():
        app.run(host="0.0.0.0", port=10000)
    t = Thread(target=run)
    t.daemon = True
    t.start()
