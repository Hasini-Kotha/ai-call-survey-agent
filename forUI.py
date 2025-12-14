import os
from flask import Flask, request, Response, render_template, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from google.cloud import firestore
from dotenv import load_dotenv
from datetime import datetime
from groq import Groq
import pytz
import time
import threading
import requests

# ----------------------------
# Load environment variables
# ----------------------------
load_dotenv()

PORT = int(os.getenv("PORT", 5000))
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
PUBLIC_URL = os.getenv("PUBLIC_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not PUBLIC_URL:
    raise Exception("❌ PUBLIC_URL missing in .env — start ngrok and add PUBLIC_URL")

# ----------------------------
# Firebase Init
# ----------------------------
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
db = firestore.Client()

# ----------------------------
# LLM (Groq)
# ----------------------------
groq = Groq(api_key=GROQ_API_KEY)

# Conversation memory
conversations = {}

# ----------------------------
# Flask App
# ----------------------------
app = Flask(__name__, template_folder="templates")

@app.get("/")
def home():
    return "AI Call Agent Running ✔"

@app.get("/dashboard")
def dashboard():
    return render_template("index.html")

# API to schedule calls
@app.post("/api/schedule")
def api_schedule():
    data = request.json
    phone = data.get("phone")
    scheduled_at = data.get("scheduledAt")

    if not phone or not scheduled_at:
        return jsonify({"error": "Missing fields"}), 400

    db.collection("scheduledCalls").add({
        "phoneNumber": phone,
        "scheduledAt": scheduled_at,
        "status": "pending",
        "createdAt": datetime.utcnow().isoformat()
    })

    return jsonify({"success": True})


# ----------------------------
# Twilio Webhook: First connection
# ----------------------------
@app.post("/voice")
def voice():
    call_sid = request.values.get("CallSid")
    print(f"📞 /voice webhook for CallSid={call_sid}")

    # Prevent duplicate greetings:
    if call_sid not in conversations:
        conversations[call_sid] = [{
            "role": "system",
            "content": (
                "You are Arjun, a friendly, calm Indian male assistant. "
                "Speak naturally and briefly. You are conducting a simple survey."
            )
        }]

    response = VoiceResponse()

    # SINGLE GATHER ONLY (no double, no fallback inside)
    gather = Gather(
        input="speech",
        action="/gather",
        method="POST",
        speech_timeout="auto",
        timeout=5  # waits properly before fallback
    )

    gather.say(
        "Hello! This is Arjun from customer care. "
        "Did you recently purchase a product from us?"
    )

    response.append(gather)

    # If silence — only then fallback
    response.say("I didn’t hear anything. Goodbye.")

    return Response(str(response), mimetype="text/xml")


# ----------------------------
# Twilio Webhook: User Speech → AI
# ----------------------------
@app.post("/gather")
def gather():
    call_sid = request.values.get("CallSid")
    user_speech = request.values.get("SpeechResult", "").strip()

    print(f"🎤 User ({call_sid}) said:", user_speech)

    # If silence → retry once
    if not user_speech:
        resp = VoiceResponse()
        resp.say("Sorry, I didn't catch that. Let’s try again.")
        
        gather = Gather(
            input="speech",
            action="/gather",
            method="POST",
            speech_timeout="auto",
            timeout=5
        )
        gather.say("Could you repeat that?")
        resp.append(gather)
        return Response(str(resp), mimetype="text/xml")

    # Add user message to memory
    conversations[call_sid].append({"role": "user", "content": user_speech})

    # LLM call
    try:
        llm = groq.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=conversations[call_sid],
            max_tokens=120
        )
        reply_text = llm.choices[0].message["content"]
        print("🤖 Arjun:", reply_text)

        conversations[call_sid].append({
            "role": "assistant",
            "content": reply_text
        })
    except Exception as e:
        print("❌ Groq error:", e)
        reply_text = "Sorry, I am facing a technical issue right now."

    # Twilio response with new gather
    resp = VoiceResponse()

    gather = Gather(
        input="speech",
        action="/gather",
        method="POST",
        speech_timeout="auto",
        timeout=5
    )
    gather.say(reply_text)
    resp.append(gather)

    resp.say("I did not hear anything. Goodbye.")
    return Response(str(resp), mimetype="text/xml")


# ----------------------------
# Make call (cron)
# ----------------------------
def make_call(phone):
    print("➡️ Starting call to", phone)
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json"

    data = {
        "To": phone,
        "From": TWILIO_PHONE,
        "Url": PUBLIC_URL + "/voice"
    }

    r = requests.post(url, data=data, auth=(TWILIO_SID, TWILIO_AUTH))
    print("📞 Twilio Response:", r.text)


# ----------------------------
# Cron job
# ----------------------------
def cron_job():
    while True:
        try:
            print("⏰ Cron: checking for scheduled calls...")

            now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
            now_iso = now_utc.isoformat(timespec="seconds")
            print("Current time (UTC):", now_iso)

            docs = (
                db.collection("scheduledCalls")
                .where("status", "==", "pending")
                .where("scheduledAt", "<=", now_iso)
                .stream()
            )

            pending = list(docs)

            if not pending:
                print("No pending calls at this time.")
            else:
                for doc in pending:
                    data = doc.to_dict()
                    phone = data["phoneNumber"]

                    print("📞 Due call found for:", phone)
                    make_call(phone)

                    db.collection("scheduledCalls").document(doc.id).update({
                        "status": "processed"
                    })

                    print("✔️ Call marked processed.")

        except Exception as e:
            print("❌ Cron error:", e)

        time.sleep(60)


# ----------------------------
# Start server + cron
# ----------------------------
if __name__ == "__main__":
    threading.Thread(target=cron_job, daemon=True).start()
    print("🚀 Flask server running on port", PORT)
    app.run(host="0.0.0.0", port=PORT)
