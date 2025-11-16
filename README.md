# OCD Loop Tracker
A full-stack web application for logging OCD-related events, tracking patterns, and analyzing emotional trends.

**Live Website:**  
https://kamran.codes

## Overview
OCD Loop Tracker is a browser-based app that allows users to securely log intrusive thoughts, triggers, compulsions, and reflections. The dashboard visualizes patterns over time and supports export for offline review.

This project includes:
- A Flask backend (REST API)
- PostgreSQL database
- Frontend hosted on GitHub Pages
- Backend deployed on Render
- Optional emotion analysis via HuggingFace Inference Router
- CSV + PDF export
- Secure authentication using bcrypt

## Features

### User Functionality
- Sign up / log in securely
- Log events with trigger, notes, and severity
- AI-assisted emotion classification (optional)
- View analytics charts (daily activity, emotions, triggers)
- Export logs as CSV or PDF

### Technical Features
- Flask REST API with structured endpoints
- PostgreSQL relational database
- Dockerized backend on Render
- HuggingFace Inference Router API integration
- GitHub Pages frontend
- Chart.js visualizations
- bcrypt password hashing
- CORS-safe frontend ↔ backend communication

## Tech Stack

**Frontend:**
- JavaScript
- HTML / CSS
- Chart.js
- GitHub Pages

**Backend:**
- Python (Flask)
- PostgreSQL
- Docker
- Render

**Emotion Analysis:**
- HuggingFace Inference Router API

## Environment Variables

Backend `.env`:
```
DATABASE_URL=<Render Postgres URL>
HF_API_KEY=<your HuggingFace key>
HF_MODEL=<model name>
```

Frontend (GitHub Pages):
```
VITE_BACKEND_URL=<Render API URL>
```

## Running Locally

### 1. Install backend dependencies
```
pip install -r requirements.txt
```

### 2. Add `.env`
```
DATABASE_URL=postgres://...
HF_API_KEY=...
HF_MODEL=...
```

### 3. Start backend
```
flask run
```

## Deployment
- **Frontend:** GitHub Pages
- **Backend:** Render (Docker)
- CORS configured for GitHub Pages → Render API communication
