---
title: DGSP
emoji: 📈
colorFrom: pink
colorTo: yellow
sdk: docker
pinned: false
app_port: 7860
---

# 🏛️ Digital Gram Samadhan Portal (DGSP)

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-000000?style=flat&logo=flask&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-4EA94B?style=flat&logo=mongodb&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2CA5E0?style=flat&logo=docker&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Hugging Face Spaces](https://img.shields.io/badge/Deployed_on-Hugging_Face-Fcd21d?logo=huggingface)

Digital Gram Samadhan Portal (DGSP) is a comprehensive, multilingual rural grievance redressal platform designed for Indian villages. It bridges the gap between rural citizens and Gram Panchayat officers, allowing users to file, track, and resolve local infrastructure and social issues digitally.

🔗 **Live Application:** [https://mridulck001-dgsp.hf.space](https://mridulck001-dgsp.hf.space)  
🔗 **GitHub Repository:** [https://github.com/mridulck001/dgsp](https://github.com/mridulck001/dgsp)

---

## ✨ Key Features

- 🗣️ **Multilingual Voice Complaints:** Powered by Sarvam AI, citizens can record complaints in 9 regional Indian languages. The system auto-chunks audio longer than 30s for seamless transcription.
- 👥 **Role-Based Access Control:**
  - **Citizens:** File complaints, track status, attach media, log GPS coordinates, and rate resolutions.
  - **Officers:** Area-specific dashboard to manage, update, and resolve assigned grievances.
  - **Admins:** Global oversight, user management, export tools, and visual analytics via Chart.js.
- ⚡ **Real-Time Updates:** Instant UI updates and collaborative commenting using Socket.IO.
- 🔔 **Notifications:** Integrated with Twilio for SMS alerts and Flask-Mail for email notifications on status changes.
- ☁️ **Cloud Storage:** Media attachments (images, videos, documents) are securely stored via Cloudinary.
- 🛡️ **Rate Limiting & Security:** Built-in rate limiting to prevent spam and Bcrypt password hashing.

---

## 🛠️ Tech Stack

- **Backend:** Python, Flask, Flask-SocketIO, Flask-Login, Flask-Limiter
- **Database:** MongoDB Atlas (PyMongo)
- **Frontend:** HTML5, CSS3, Vanilla JavaScript, Jinja2 Templates, Chart.js
- **AI/ML:** Sarvam AI API (Speech-to-Text)
- **Audio Processing:** Pydub, FFmpeg (for >30s audio chunking)
- **External APIs:** Cloudinary, Twilio
- **Deployment:** Docker, Hugging Face Spaces

---

## 🚀 Local Installation & Setup

### Prerequisites

- Python 3.9+
- MongoDB (Local installation or Atlas URI)
- **FFmpeg** installed on your system (Required for Pydub audio chunking)
  - **Windows:** `winget install ffmpeg` or download from gyan.dev
  - **Ubuntu/Debian:** `sudo apt-get install ffmpeg`
  - **Mac:** `brew install ffmpeg`

### 1. Clone the repository

```bash
git clone [https://github.com/mridulck001/dgsp.git](https://github.com/mridulck001/dgsp.git)
cd dgsp
```

### 2. Install dependencies

It is recommended to use a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Variables

Create a `.env` file in the root directory and add the following keys. 
*Note: The app will gracefully fall back to local defaults or skip external services if keys are missing.*

```env
# Flask & DB
SECRET_KEY=your_super_secret_key_here
MONGO_URI=mongodb+srv://<user>:<password>@cluster.mongodb.net/dgsp?retryWrites=true&w=majority

# Sarvam AI (Required for Voice Input)
SARVAM_API_KEY=your_sarvam_api_key

# Cloudinary (Required for Attachments)
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

# Twilio (Optional: For SMS alerts)
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=your_twilio_number
APP_URL=http://localhost:5000

# Email (Optional: For Email alerts)
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
```

### 4. Run the Application

Because the app uses `eventlet` for asynchronous Socket.IO, run it directly via Python:

```bash
python app.py
```

Access the portal at `http://localhost:5000`.

---

## 🔐 Default Demo Credentials

On the first run, the app automatically seeds a default Admin account:

- **Phone (Identifier):** `9000000000`
- **Password:** `Admin@123`

---

## 🐳 Docker Deployment (Hugging Face)

This project is pre-configured to run on Hugging Face Spaces using Docker. The included `Dockerfile`:
- Uses a lightweight Python 3.9 base image.
- Installs `ffmpeg` natively.
- Installs Python dependencies via `requirements.txt`.
- Exposes port `7860` (default for Hugging Face).

To build and run locally with Docker:

```bash
docker build -t dgsp-app .
docker run -p 7860:7860 dgsp-app
```

Access the app at `http://localhost:7860`.

---

## 📂 Project Structure

```text
dgsp/
├── app.py                  # Main Flask application (Routes, Sockets, Models)
├── Dockerfile              # Docker configuration for deployment
├── requirements.txt        # Python dependencies
├── static/
│   └── css/
│       └── style.css       # Custom stylesheet (Rural-India theme)
└── templates/              # Jinja2 HTML templates
    ├── base.html           # Main layout and navbar
    ├── index.html          # Landing page
    ├── login.html          # Authentication
    ├── register.html       # User onboarding
    ├── dashboard_*.html    # Role-specific dashboards
    ├── complaint_new.html  # Complaint filing with Voice & Maps
    ├── complaint_view.html # Individual complaint details & timeline
    └── error.html          # Custom error pages
```

---

## 📄 License

This project is open-source and available under the [MIT License](LICENSE).

<div align="center">
  <b>Designed for Bharat 🇮🇳</b>
</div>
