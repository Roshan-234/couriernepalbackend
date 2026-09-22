import os
os.environ["FLASK_ENV"] = "default"
from app import create_app
app = create_app("default")
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False, debug=False)
