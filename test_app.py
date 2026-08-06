from flask import Flask
import os

app = Flask(__name__)
port = int(os.getenv("PORT", 8080))

@app.route('/')
def home():
    return "OK"

@app.route('/health')
def health():
    return "OK", 200

# ЭТО ОБЯЗАТЕЛЬНО для python test_app.py
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=port)
