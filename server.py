import os
import datetime

from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, firestore

from apscheduler.schedulers.background import BackgroundScheduler

from groq import Groq  # Groq LLM client


# -----------------------------
# 1) ENV + GLOBAL INIT
# -----------------------------
load_dotenv()

PORT = int(os.getenv("PORT", 5000))

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
PUBLIC_URL = os.getenv("PUBLIC_URL")  # e.g. https://xxx.ngrok-free.app

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.1-8b-instant"  # good fast model

if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE, PUBLIC_URL, GROQ_API_KEY]):
    print("ERROR: Missing one or more required env vars. Check .env file.")
    exit(1)

# Firebase
cred = credentials.Certificate(os.path.abspath(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")))
firebase_admin.initialize_app(cred)
db = firestore.client()

# Twilio client
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Groq client
groq_client = Groq(api_key=GROQ_API_KEY)

# In-memory conversation context: { callSid: [messages] }
conversations = {}

SYSTEM_PROMPT = """
You are ARJUN, a friendly Indian male customer-support survey assistant.

Your role:
- Call a customer and collect meaningful feedback.
- Speak naturally in short, conversational sentences.
- Ask ONE question at a time.
- Adapt your questions based on the product they purchased.

Conversation Flow (Dynamic):
1. Start by asking if they recently purchased a product.
2. Ask what product they bought.
3. Detect the product type and automatically choose relevant follow-up questions.
4. Ask 4–6 short, meaningful questions related to THAT product.
5. Keep the tone warm, helpful, and human.
6. End politely with a thank you.

Product Understanding Rules:
- Identify the product category from the user's words.
- Ask questions suitable for that category.
- Examples:
    • If they bought a dog → ask about dog chain availability, training, food quality, health, vaccinations, grooming, behaviour.
    • If they bought electronics → ask about performance, battery, heating, installation experience.
    • If they bought clothing → ask about size, material, comfort, fitting, delivery.
    • If they bought groceries → ask about freshness, packaging, taste.
    • If they bought home appliances → ask about installation, noise, energy usage, ease of use.
    • If they bought cosmetics → ask about fragrance, sensitivity, texture, results.

When they answer:
- Briefly acknowledge what they said.
- Continue the survey with the next relevant question.
- Never ask unrelated or generic questions.
- Never ask multiple questions at once.

If the user says they didn’t buy anything:
- Apologize politely and end the call early.

Speak like a friendly Indian support agent.
Keep everything short, simple, and phone-friendly.
"""

# -----------------------------
# 2) HELPER: CALL GROQ LLM
# -----------------------------
def generate_ai_reply(call_sid: str, user_text: str) -> str:
    """
    Use Groq LLM to generate Arjun's reply.
    """
    if call_sid not in conversations:
        conversations[call_sid] = [{"role": "system", "content": SYSTEM_PROMPT}]

    conversations[call_sid].append({"role": "user", "content": user_text})

    try:
        chat_completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=conversations[call_sid],
            max_tokens=120,
            temperature=0.7,
        )
        reply = chat_completion.choices[0].message.content.strip()
    except Exception as e:
        print(" Error calling Groq:", e)
        reply = "Sorry, I am having trouble right now. We will contact you again later."

    # store assistant message and trim history
    conversations[call_sid].append({"role": "assistant", "content": reply})
    conversations[call_sid] = conversations[call_sid][-10:]

    print("Arjun:", reply)
    return reply


# -----------------------------
# 3) FLASK APP
# -----------------------------
app = Flask(__name__)


@app.get("/")
def health():
    return "Python AI Call Survey Agent (Twilio + Groq + Firestore) is running."


# 4) When call connects: /voice
@app.post("/voice")
def voice():
    call_sid = request.form.get("CallSid", "unknown")
    print(f" /voice webhook for CallSid={call_sid}")

    if call_sid not in conversations:
        conversations[call_sid] = [{"role": "system", "content": SYSTEM_PROMPT}]

    vr = VoiceResponse()

    # Small pause to avoid cutting start of prompt
    vr.pause(length=1)

    # Start gather (Twilio STT)
    gather = vr.gather(
        input="speech",
        action="/gather",
        method="POST",
        timeout=8,
        speech_timeout="auto",
    )

    gather.say(
        "Hello, this is Arjun from customer support. "
        "I would like to ask a few quick questions about your experience with our product. "
        "Did you recently buy our product?"
    )

    # If user says nothing, retry once
    vr.say("I did not hear anything. Let me try once more.")
    vr.redirect("/voice")

    return Response(str(vr), mimetype="text/xml")


# 5) Handle each user utterance: /gather
@app.post("/gather")
def gather():
    call_sid = request.form.get("CallSid", "unknown")
    user_text = request.form.get("SpeechResult", "") or ""

    print(f"🎤 User ({call_sid}) said:", user_text)

    vr = VoiceResponse()

    if not user_text.strip():
        vr.say("I did not catch that. Please say it again.")
        vr.redirect("/voice")
        return Response(str(vr), mimetype="text/xml")

    # Get AI reply from Groq
    reply = generate_ai_reply(call_sid, user_text)

    # Read reply & listen again
    gather = vr.gather(
        input="speech",
        action="/gather",
        method="POST",
        timeout=8,
        speech_timeout="auto",
    )
    gather.say(reply)

    # Fallback if silence next time
    vr.say("I did not hear anything. I will end this call now. Thank you for your time. Goodbye.")

    return Response(str(vr), mimetype="text/xml")


# -----------------------------
# 4) OUTBOUND CALL + SCHEDULER
# -----------------------------
def make_call(phone_number: str):
    """
    Trigger a Twilio outbound call to phone_number.
    Twilio will hit /voice when call is answered.
    """
    try:
        print("➡️ Starting call to", phone_number)
        call = twilio_client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE,
            url=f"{PUBLIC_URL}/voice",
            timeout=30,
        )
        print("Call initiated, SID:", call.sid)
    except Exception as e:
        print(" Error making call:", e)


def check_scheduled_calls():
    """
    Check Firestore 'scheduledCalls' for pending calls whose scheduledAt <= now.
    scheduledAt must be stored as ISO string in UTC (e.g. 2025-12-10T16:00:00.000Z).
    """
    print("⏰ Cron: checking for scheduled calls...")
    now_iso = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    print("Current time (UTC):", now_iso)

    try:
        docs = (
            db.collection("scheduledCalls")
            .where("status", "==", "pending")
            .where("scheduledAt", "<=", now_iso)
            .stream()
        )

        processed_any = False
        for doc in docs:
            data = doc.to_dict()
            phone = data.get("phoneNumber")
            print(" Due call found for:", phone)
            if phone:
                make_call(phone)
                doc.reference.update({"status": "processed"})
                processed_any = True

        if processed_any:
            print("✔️ Marked due calls as processed.")
        else:
            print("No pending calls at this time.")
    except Exception as e:
        print("Error in cron job:", e)


# -----------------------------
# 5) MAIN ENTRYPOINT
# -----------------------------
if __name__ == "__main__":
    # Start scheduler (runs every minute)
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(check_scheduled_calls, "interval", minutes=1)
    scheduler.start()

    print(f" Flask server running on port {PORT}")
    app.run(host="0.0.0.0", port=PORT)
