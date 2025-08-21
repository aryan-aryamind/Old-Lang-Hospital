from flask import Flask, jsonify
from doctor_appointments import bp_doctor
from lab_appointments import bp_lab
import logging
import os

# Shared user_sessions dict (if needed by blueprints, import or pass as needed)
user_sessions = {}

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Register blueprints
app.register_blueprint(bp_doctor)
app.register_blueprint(bp_lab)

@app.route('/')
def index():
    return jsonify({"status": "ok", "message": "Hospital RAG API is running."})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)