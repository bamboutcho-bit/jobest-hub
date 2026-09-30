# Job Search & Freelance Client Acquisition Automation

An autonomous, 24/7 client lead generation and job search engine powered by AI and robust heuristic fallbacks. Scrapes major job boards and freelance client feeds, scores candidate fit, drafts tailored proposals, tracks application threads, and alerts your phone the moment a client or recruiter responds.

---

## ⚡ Quick Start: Running with Docker (Recommended)

Docker provides the complete 24/7 background experience: it bundles PostgreSQL, Ollama LLM, Playwright headless Chromium, the automated discovery loop, the inbox reply monitor, and the web dashboard in isolated, self-healing containers.

### 1. Prerequisites
* [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running (or Docker Engine on Linux/VPS).

### 2. Setup & Configuration
1. **Configure Environment:** Ensure `.env` is present in the root folder (or copy `.env.example` to `.env`):
   ```bash
   cp .env.example .env
   ```
2. **Add Your Resume:** Place your CV at:
   ```
   candidate_data/resume.pdf
   ```
3. **Set Your Credentials in `.env`:**
   * `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` (for dashboard login)
   * `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID` (optional, for instant phone alerts)
   * `SMTP_*` & `IMAP_*` (optional, for automated outreach and reply tracking)

### 3. Build & Run
On the first run on a new machine, build the image and start all services in the background with a single command:

```bash
docker compose up --build -d
```

### 4. Access the Dashboards
Once running, open your browser:
* **Admin & Job Search Dashboard:** [http://localhost:8080](http://localhost:8080)
* **Freelance Client Acquisition Hub:** [http://localhost:8080/freelance](http://localhost:8080/freelance)

### 5. Managing Docker
* **View live logs:**
  ```bash
  docker compose logs -f
  ```
* **Check service health:**
  ```bash
  docker compose ps
  ```
* **Stop all services:**
  ```bash
  docker compose down
  ```
* **Start back up anytime (no rebuild needed):**
  ```bash
  docker compose up -d
  ```

---

## 💻 Option 2: Running Locally Without Docker

If you prefer to run directly on your host operating system (Windows, macOS, or Linux) using Python:

### 1. Create and Activate Virtual Environment
```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Start the Web Dashboard
```bash
python -m uvicorn src.dashboard.app:app --host 0.0.0.0 --port 8080
```
Open [http://localhost:8080](http://localhost:8080) in your browser. (Database automatically falls back to local SQLite `jobsearch_local.db` if PostgreSQL is not running).

### 4. Run Discovery & Automation Loops
In a separate terminal window:
* **Run automated Job & Freelance Discovery cycle on schedule:**
  ```bash
  python scripts/run_pipeline.py --loop --interval-hours 4
  ```
* **Run continuous Inbox Reply Monitor:**
  ```bash
  python scripts/check_inbox.py --loop
  ```

---

## 🎯 Core Features

### 1. Freelance Client Acquisition Engine (`/freelance`)
* **Multi-Channel Discovery:** Continuously monitors Hacker News (*"Who is hiring?"*), Reddit (*r/forhire*, *r/freelance_forhire*), Jobicy, and RemoteOK.
* **Spam & Equity Filtering:** Automatically discards unpaid requests, equity-only offers, and advertisements.
* **Strategic Proposal Angles:**
  * `mvp_speed` (⚡ Speed & Rapid MVP): 7–14 day working prototype roadmap.
  * `architecture_quality` (🛡️ Robust Architecture): Clean domain design, test coverage, and Dockerized microservices.
  * `roi_advisory` (💡 ROI & Solution Advisory): Cost optimization, lean milestone delivery, and technical audit.
* **5-Point Conversion Blueprint:** Hook $\rightarrow$ 3-Step Roadmap $\rightarrow$ Tech Proof $\rightarrow$ Milestone Pricing $\rightarrow$ Low-friction CTA.
* **Smart Follow-Up Cadence:** Automated 2-step follow-up scheduler (+3 days for Architecture Deep-Dive, +4 days for Roadmap on File) where 60%+ of freelance deals close. Includes live status badges, automated cutoffs, and 1-click execution.
* **Deal Pricing Calculator:** Live duration $\times$ daily rate calculator prefilled from your active candidate profile.

### 2. Full-Time Job Hunting (`/`)
* **Multi-Board Scraping:** Integrates JobSpy across LinkedIn, Indeed, Glassdoor, and ZipRecruiter with public remote feeds (Himalayas, WeWorkRemotely, Arbeitnow).
* **Deep Evaluation:** Scores technical match percentage, extracts recruiter emails, and validates visa sponsorship / relocation signals.
* **Safety Controls:** Daily outbound sending caps, suppression lists, idempotency keys, and kill-switches.

### 3. Dynamic Candidate Profile Management
* Configure multiple profiles with custom titles, core stacks, target locations, rates, and keywords.
* Search query matrices and AI prompts adapt dynamically at runtime to whoever is the active candidate.

---

---

## 🤝 Sharing with Friends ($0 Free Forever Guide)

You can share this package with any developer friend or colleague so they can run their own job and client pipeline for **$0 forever**.

### Why It Costs $0 Forever:
* **No Paid AI APIs Needed:** Runs out-of-the-box using the 100% offline heuristic generation engine, or free local Ollama (`llama3.2:3b` in Docker). You never need OpenAI, Anthropic, or credit cards.
* **No Paid Email / SMTP Needed:** Pitch Studio includes **1-Click Multi-Channel Formats**:
  * 💼 **LinkedIn Connection Notes:** Tailored message strictly $\le 300$ characters for instant invite requests.
  * 💬 **Chat DMs:** Casual 3-sentence DM with booking link ready to paste into Reddit, Discord, Twitter/X, or Slack.
  * ✉️ **Full Email Pitch:** Complete 3-sprint technical proposal.
  * ⏰ **Smart Follow-Ups #1 & #2:** Ready to copy and paste on Day 3 and Day 6.
  * *Simply hit **"📋 Copy to Clipboard"** and paste directly to your client or lead!*
* **Free Calendar Integration:** Add your free [Cal.com](https://cal.com) or [Calendly](https://calendly.com) link in the Candidate Profile or `.env` (`CALENDAR_BOOKING_URL`). The system injects it into every proposal CTA.
* **Pre-Seeded & Ready:** Comes pre-packaged with `jobsearch_local.db` containing **421 curated jobs** and **44 active freelance leads** so your friends can immediately browse, evaluate, and pitch clients from minute 1.

### 60-Second Friend Quickstart:
1. Unzip `build_jobsearch_ready.zip` (clean archive, ~1 MB).
2. Open terminal in the unzipped folder:
   ```bash
   docker compose up --build -d
   ```
3. Open `http://localhost:8080/freelance` (Default login: `admin` / `password123`).
4. Go to **Candidate Profile**, type your name, stack, and free Cal.com link, click **Save Profile**!

---

## 📦 Transferring & Running on Another Laptop

### 1. Extract & Open
1. Copy **`build_jobsearch_ready.zip`** (only ~1 MB, all heavy OS `.venv` and cache binaries cleaned) to your other laptop.
2. Extract it into your project folder.

### 2. Checklist: What to Update on Your New Machine
Before launching, verify or update these personalized files:
* **Your Resume:** Replace [`candidate_data/resume.pdf`](file:///c:/Users/aeibo/Downloads/build_jobsearch/build_jobsearch/candidate_data/resume.pdf) with your own PDF CV.
* **Dashboard Password:** In `.env`, set `DASHBOARD_PASSWORD` to your own secret password.
* **Your Identity:** In `.env`, update `SENDER_DISPLAY_NAME` and your contact info.
* **Calendar Booking Link (Optional):** In `.env`, set `CALENDAR_BOOKING_URL` (e.g. `https://cal.com/username/15min`).
* **Outbound Email (Optional):** In `.env`, set `SMTP_USER`, `SMTP_PASSWORD` (e.g. Gmail App Password) if you want automated outbound emails sent directly from your address.
* **Telegram Alerts (Optional):** In `.env`, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to receive immediate push notifications on your phone whenever a recruiter or client responds.

### 3. Launch with Docker
```bash
docker compose up --build -d
```
All containers (Web UI, background scrapers, PostgreSQL, Ollama local AI) will build and run autonomously. Access the UI at:
* **Freelance Hub:** `http://localhost:8080/freelance`
* **Job Hunter:** `http://localhost:8080`

---

## 🧪 Running Tests

To run the automated test suite across all modules (66 tests):
```bash
pytest -v
```
