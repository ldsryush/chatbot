import os
import json
import requests
from flask import Flask, request, jsonify, send_from_directory
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Retrieve our credentials from the environment
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGO_CONN_STR = os.getenv("MONGO_CONN_STR")

# -----------------------------
# Appointment System using MongoDB
# -----------------------------
class AppointmentSystem:
    def __init__(self):
        try:
            self.client = MongoClient(MONGO_CONN_STR)
            self.db = self.client["AppointmentDB"]  # Change this if your database name is different
            self.collection = self.db["Appointments"]  # Change this if your collection name is different
            self.client.admin.command("ping")
            print("Connected to MongoDB successfully.")
        except Exception as e:
            print("Error connecting to MongoDB:", e)
    
    def add_appointment(self, name, date, time):
        if self.collection.find_one({"date": date, "time": time}):
            return False, "The slot is already booked."
        else:
            self.collection.insert_one({"name": name, "date": date, "time": time})
            return True, f"Appointment booked for {name} on {date} at {time}."
    
    def cancel_appointment(self, name, date, time):
        result = self.collection.delete_one({"name": name, "date": date, "time": time})
        if result.deleted_count > 0:
            return True, f"Appointment cancelled for {name} on {date} at {time}."
        else:
            return False, "No matching appointment found to cancel."
    
    def check_availability(self, date, time):
        return self.collection.find_one({"date": date, "time": time}) is None
    
    def get_schedule(self):
        appointments = list(self.collection.find({}, {"_id": 0}))
        return sorted(appointments, key=lambda x: (x["date"], x["time"]))

# Instantiate the appointment system
appointment_system = AppointmentSystem()

# -----------------------------
# Flask Application Setup
# -----------------------------
app = Flask(__name__, static_url_path="")

def interpret_message(message):
    """
    Calls the PaLM (Gemini) API directly via an HTTP POST to extract scheduling intent.
    The prompt instructs the model to return a JSON object with exactly these keys:
      - "action": one of "book", "cancel", "reschedule", or "unknown"
      - "name": customer's name (if mentioned)
      - "date": appointment date (in YYYY-MM-DD format)
      - "time": appointment time (in HH:MM format)
      - "new_date" and "new_time": for rescheduling (if applicable)
    """
    prompt = f"""
You are an intelligent assistant for scheduling appointment conversations for a window washing company.
Extract the intent of the following message regarding an appointment and return a JSON object with exactly these keys:
- "action": one of "book", "cancel", "reschedule", or "unknown"
- "name": customer's name if mentioned (otherwise leave it blank)
- "date": appointment date in YYYY-MM-DD format if mentioned
- "time": appointment time in HH:MM format if mentioned
- "new_date": for reschedule cases, the new date if mentioned
- "new_time": for reschedule cases, the new time if mentioned

User message: "{message}"

Return only the JSON object.
"""
    # Endpoint URL for PaLM (Gemini) – adjust if the version or endpoint changes.
    url = f"https://generativelanguage.googleapis.com/v1beta2/models/text-bison-001:generateText?key={GEMINI_API_KEY}"
    payload = {
        "prompt": {"text": prompt},
        "temperature": 0.0,
        "maxOutputTokens": 256,
        "topP": 0.95
    }
    try:
        r = requests.post(url, json=payload)
        if r.status_code == 200:
            result = r.json()
            if "candidates" in result and len(result["candidates"]) > 0:
                generated_text = result["candidates"][0].get("output")
                if generated_text:
                    try:
                        # Attempt to parse the generated text as JSON
                        data = json.loads(generated_text)
                    except Exception as e:
                        print("Error parsing JSON from generated text:", e)
                        data = {"action": "unknown"}
                else:
                    data = {"action": "unknown"}
            else:
                data = {"action": "unknown"}
        else:
            print("Error from PaLM API:", r.text)
            data = {"action": "unknown"}
    except Exception as e:
        print("Error calling PaLM API:", e)
        data = {"action": "unknown"}
    return data

@app.route("/")
def home():
    return send_from_directory("", "index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")
    
    # Get the interpretation from the PaLM API
    interpretation = interpret_message(user_message)
    action = interpretation.get("action", "unknown")
    name = interpretation.get("name", "")
    date = interpretation.get("date", "")
    time_ = interpretation.get("time", "")
    new_date = interpretation.get("new_date", "")
    new_time = interpretation.get("new_time", "")
    
    response_text = ""
    if action == "book":
        if appointment_system.check_availability(date, time_):
            success, resp_msg = appointment_system.add_appointment(name, date, time_)
            response_text = resp_msg
        else:
            response_text = "Sorry, that slot is already booked."
    elif action == "cancel":
        success, resp_msg = appointment_system.cancel_appointment(name, date, time_)
        response_text = resp_msg
    elif action == "reschedule":
        success, cancel_msg = appointment_system.cancel_appointment(name, date, time_)
        if success:
            if appointment_system.check_availability(new_date, new_time):
                success2, book_msg = appointment_system.add_appointment(name, new_date, new_time)
                response_text = f"Old appointment cancelled. {book_msg}"
            else:
                response_text = "Sorry, the new slot is already booked."
        else:
            response_text = "Couldn't cancel the existing appointment. Please check your details."
    else:
        response_text = "I'm sorry, I didn't understand your request. Can you please rephrase?"
    
    updated_schedule = appointment_system.get_schedule()
    return jsonify({"response": response_text, "schedule": updated_schedule})

if __name__ == "__main__":
    app.run(port=5000, debug=True)
