 AI Call Survey Agent

An AI-powered automated call survey system that conducts real-time, conversational voice surveys using Large Language Models.  
The agent dynamically adapts questions based on user responses and schedules outbound calls automatically.

Built for real-world customer feedback collection.

---

 Features

- 📞 **Automated Voice Calls** using Twilio
- 🧠 **Real-time Conversational AI** powered by Groq LLM
- 🎙️ **Speech-to-Text (STT)** and **Text-to-Speech (TTS)** via Twilio
- 🔁 **Dynamic Question Flow** based on product type and user responses
- ⏰ **Scheduled Outbound Calls** using background cron jobs
- 🔥 **Firebase Firestore** for call scheduling and status tracking
- 🌐 **Flask-based Backend API**

---

 How It Works

1. Phone numbers and call schedules are stored in **Firebase Firestore**
2. A background scheduler checks for due calls every minute
3. When a call is due:
   - Twilio initiates an outbound call
   - The call connects to the Flask `/voice` webhook
4. User speech is converted to text (STT)
5. The text is sent to **Groq LLM**
6. AI generates the next context-aware question
7. Response is converted back to speech (TTS)
8. The conversation continues dynamically until completion

---

 🛠️ Tech Stack

- **Backend:** Python, Flask  
- **Voice & Telephony:** Twilio (Voice, STT, TTS)  
- **AI / LLM:** Groq (LLaMA 3.1)  
- **Database:** Firebase Firestore  
- **Scheduling:** APScheduler (Cron-based background jobs)  
- **Deployment:** Ngrok / Cloud VM / Container-ready  

---

 Project Structure

ai-call-survey-agent/
│
├── app.py # Main Flask application
├── .env.example # Environment variable template
├── requirements.txt # Python dependencies
└── README.md

yaml
Copy code

---
 Environment Variables

Create a `.env` file with the following:

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE=
PUBLIC_URL=

GROQ_API_KEY=

GOOGLE_APPLICATION_CREDENTIALS=
PORT=5000

yaml
Copy code



 Running the Project

```bash
pip install -r requirements.txt
python app.py

ngrok http 5000


bash
Copy code
ngrok http 5000
