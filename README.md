# ⚡ Voice Desktop OS

A dual-tier, voice-controlled desktop automation system combining:
1. **Speech-to-Speech Voice Agent**: Real-time natural conversational pipeline with barge-in interruption tuning, extracted from LiveKit Agents + Gemini Live (`gemini-3.1-flash-live-preview`).
2. **System One Reflex Worker (Jev)**: Fast-path (150–250ms) decision engine powered by TypeSafe Jev (`jev-latest`), executing deterministic window management, UI element clicks, and keyboard inputs using structured `Choice`, `Noul`, and `Score` primitives.
3. **System Two Worker (Multimodal LLM)**: Deep reasoning, coding, and Set-of-Marks (SoM) visual grounding agent powered by Gemini (`gemini-3.6-flash`), equipped with tools inspired by **Hermes Agent** and **Pi Agent**.
4. **Desktop Automation & CUA**: Native Windows UI Automation, Win32 window management, mouse/keyboard CUA controls, asynchronous PowerShell terminal execution, file editing, and CDP browser automation.

---

## 🏗️ Architecture Overview

```
User Voice / Speech Input
           │
           ▼
[LiveKit + Gemini Live Realtime S2S Agent]
           │
           │ (calls control_desktop tool)
           ▼
 [Master Orchestrator]
           │
           ├───► [Jev System One Reflex] (Evaluates State Snapshot)
           │          ├── Action Type (Choice)
           │          ├── Target Window / Element (Choice)
           │          └── Needs Escalation? (Noul)
           │
           ├───► [Fast-Path Direct Execution] (< 1.5s)
           │          ├── Bring Window to Front (SetForegroundWindow)
           │          ├── Click Element (UI Automation / Coordinates)
           │          └── Keyboard Shortcuts & Scrolling
           │
           └───► [System Two Escalation (Complex LLM Worker)]
                      ├── Set-of-Marks Screenshot Grounding
                      ├── Multi-step PowerShell / CLI Execution (Hermes)
                      ├── Resilient File Reading & Patching (Pi)
                      └── Visual Problem Solving & DOM Navigation
```

---

## 🚀 Quickstart

### 1. Requirements & Dependencies
Ensure Python 3.11+ is installed. Dependencies are listed in `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 2. Configuration (`.env`)
The `.env` file contains your verified credentials:
```ini
TYPESAFE_API_KEY="apikey_..."
JEV_BASE_URL="https://api.typesafe.ai/v1/systemone"
JEV_MODEL="jev-latest"

GEMINI_API_KEY="AIzaSy..."
GOOGLE_API_KEY="AIzaSy..."
COMPLEX_LLM_MODEL="gemini-3.6-flash"
GEMINI_LIVE_MODEL="gemini-3.1-flash-live-preview"

LIVEKIT_URL="wss://..."
LIVEKIT_API_KEY="..."
LIVEKIT_API_SECRET="..."
```

---

## 💻 Running the App (All-in-One)

You can launch the entire stack (FastAPI server, LiveKit voice agent, local microphone audio, and interactive CLI) with a single command:

```bash
python run.py
```
*(or `python run.py start`)*

### What happens when you run `python run.py`:
1. 🌐 **FastAPI Server** starts in the background on `http://127.0.0.1:8000`.
2. 🎙️ **LiveKit Voice Agent** starts in the background connected to Gemini Live.
3. 🎧 **Local Microphone & Speakers** activate automatically so you can immediately speak aloud to control your PC.
4. 💻 **Interactive CLI Console** opens in your terminal with live telemetry from Jev System One and the LLM worker.

---

### Alternative Subcommands (Standalone Modes)
If you ever want to run components individually:
* `python run.py chat` — Interactive CLI only (without starting background voice worker/mic).
* `python run.py server` — FastAPI backend only (`http://127.0.0.1:8000/docs`).
* `python run.py worker start` — Speech-to-speech LiveKit worker only.
* `python run.py mic` — Connect microphone/speakers to a specific room.

---

## 🧰 Tools Integrated

* **From `yikangy873-gif/jev-desktop`**: Windows accessibility tree inspector (`ui_inspector`) extracting buttons, inputs, tabs, and bounding boxes.
* **From `wy-coliney/jev-browser-use`**: Chrome DevTools Protocol & Playwright DOM interactive tree querying.
* **From `nousresearch/hermes-agent`**: Asynchronous PowerShell process runner with output capture and background task tracking.
* **From `earendil-works/pi`**: Resilient file reader, writer, and exact substring patcher.
* **Computer Use Agent (CUA)**: Unified mouse clicking, coordinate clamping, smooth dragging, text typing, and hotkey sequences.
