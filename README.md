# KuhaMo

A Django-based web application for field inspection and reporting, built as a 2026 capstone project.

---

## Table of Contents

- [Project Overview](#project-overview)
- [Tech Stack](#tech-stack)
- [Features](#features)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Environment Variables](#environment-variables)
  - [Database Setup](#database-setup)
  - [Running the Project](#running-the-project)
- [Project Structure](#project-structure)
- [Development Guidelines](#development-guidelines)
- [Deployment](#deployment)
- [Contributing](#contributing)
- [License](#license)

---

## Project Overview

KuhaMo is a full-stack web application designed to digitize field inspection workflows. It enables field personnel to submit inspection reports with photos, findings, and recommendations, while administrators can review, manage, and export reports through a structured dashboard. The system supports user roles, report statuses, notifications, and job order PDF generation.

This thesis/capstone project was developed in 2026, focusing on real-world usability, clean architecture, and a smooth user experience for both field users and administrators.

---

## Tech Stack

| Category        | Technology                          |
|-----------------|-------------------------------------|
| Backend         | Python, Django                      |
| Frontend        | HTML, Tailwind CSS, Vanilla JS      |
| Database        | SQLite (dev), PostgreSQL (prod)     |
| Styling         | Tailwind CSS (via django-tailwind)  |
| PDF Generation  | WeasyPrint / wkhtmltopdf            |
| Email          | Django email backend (SMTP)         |
| Environment     | `.env` via `python-decouple`        |

---

## Features

### For Field Users
- Submit inspection reports with photos, findings, and recommendations
- View and download job order PDFs
- Track report status

### For Administrators
- Dashboard with report overview and filtering
- Review and manage reports (approve, close, etc.)
- Notification system for report actions
- Materials requirement field for reports
- Custom dropdown (CDD) UI component for selects and filters

### General
- Role-based access control
- Responsive UI
- Email notifications for key events
- Migration-driven schema changes

---

## Getting Started

### Prerequisites

- Python 3.10+ (match the version used in production if known)
- pip / pipenv / poetry (depending on your setup)
- Node.js (only if you plan to rebuild Tailwind CSS from source)
- A supported browser (Chrome, Firefox, Edge)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd <project-directory>
   ```

2. **Create and activate a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate        # On Windows: venv\Scripts\activate
   ```

3. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

   If the project uses Pipenv, use:
   ```bash
   pip install pipenv
   pipenv install
   ```

4. **Install Node dependencies (if applicable)**
   ```bash
   npm install
   ```

5. **Configure environment variables**
   Copy `.env.example` to `.env` if present, then fill in the required values.
   ```bash
   cp .env.example .env
   ```

   If there is no `.env.example`, create a `.env` file based on the [Environment Variables](#environment-variables) section.

6. **Apply database migrations**
   ```bash
   python manage.py migrate
   ```

7. **Create a superuser (optional, for admin access)**
   ```bash
   python manage.py createsuperuser
   ```

8. **Collect static files (for production or when needed)**
   ```bash
   python manage.py collectstatic
   ```

### Environment Variables

Use a `.env` file for local development. Do not commit secrets to version control.

Copy `.env.example` to `.env` and fill in the values. It lists every variable the project reads.

`DEBUG` is **off unless `.env` sets `DEBUG=True`**. For local development and testing, keep `DEBUG=True`; otherwise the dev server stops serving CSS/JS and the pages look broken.

### Testing with DEBUG off (before deployment)

To see the site exactly as the live server will serve it:

```bash
python manage.py tailwind build
python manage.py collectstatic --noinput
```

Then set `DEBUG=False` in `.env` (plus `SECURE_COOKIES=False` if you open it over plain `http://`, or sign-in will silently fail) and run the server. Static files are served by WhiteNoise.

### Deploy checklist

- `DEBUG=False`, and real values for `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `SITE_URL`
- Leave `SECURE_COOKIES` unset (on) — the live site must use HTTPS
- `python manage.py tailwind build` and `python manage.py collectstatic --noinput` on every deploy (the built Tailwind CSS is not committed)
- `python manage.py migrate`
- `python manage.py check --deploy` and fix what it reports

### Database Setup

By default, the project may use SQLite for local development. If the project is configured for PostgreSQL or another database in production, set the corresponding environment variables before running migrations.

```bash
python manage.py migrate
```

If you need a fresh database for testing:
```bash
python manage.py flush
# or recreate the database and re-run migrations
```

### Running the Project

**Development server**
```bash
python manage.py runserver
```

**With Tailwind CSS watching (if used)**
```bash
npm run dev
```

Visit `http://127.0.0.1:8000/` in your browser.

---

## Project Structure

```
<project-root>/
├── manage.py
├── Pipfile
├── Pipfile.lock
├── .env
├── .env.example
├── README.md
├── config/               # Django project settings (settings.py, urls.py, wsgi.py, asgi.py)
├── adm/                 # Admin-related app
├── user/                # User/front-end app
├── accounts/            # Authentication / accounts app
├── theme/               # Shared templates, static assets, Tailwind source
├── media/               # User-uploaded files
├── ml/                  # ML-related module (if used)
└── ...
```

---

## Development Guidelines

### General
- Keep commits small and focused on one change at a time.
- Use clear commit messages that explain the “what” and “why.”
- Do not commit secrets, `.env` files, or large binary backups.
- Keep the `README.md` in sync with the current setup and run instructions.

### Code Style
- Follow existing project conventions for formatting and naming.
- Use meaningful variable and function names.
- Keep templates readable and avoid overly nested logic where possible.
- Prefer small, reusable components or partials for repeated UI patterns.

### Branching
- Use feature branches for new work (for example `feature/<description>`).
- Use fix branches for bug fixes (for example `fix/<description>`).
- Keep branch names short, lowercase, and descriptive.

### Database Changes
- Always create and test migrations locally before sharing.
- Do not edit migration files after they have been shared.
- Confirm migration order when multiple migrations touch the same models.

### Static Assets
- Place shared static assets in the appropriate `static/` or `theme/static/` directory.
- Rebuild Tailwind CSS when styles change, if the project uses a Tailwind build step.
- Avoid committing generated build artifacts unless the project explicitly requires it.

---

## Deployment

Deployment steps depend on the hosting setup. Common steps include:

1. Set `DEBUG=False` in production.
2. Configure `ALLOWED_HOSTS`.
3. Use a production-ready database (for example PostgreSQL).
4. Configure a production email backend if email features are used.
5. Collect static files:
   ```bash
   python manage.py collectstatic
   ```
6. Run migrations on the production database:
   ```bash
   python manage.py migrate
   ```
7. Use a proper WSGI/ASGI server (for example Gunicorn, uWSGI) behind a reverse proxy if needed.

Do not store development settings or secrets in version control.

---

## Contributing

1. Fork or branch from the relevant base branch.
2. Make your changes in small, reviewable commits.
3. Update the README if your change affects setup, environment variables, or run instructions.
4. Test your changes locally before opening a pull request.
5. Keep pull requests focused and descriptive.

---

## License

This project is a capstone project. If a license is required, add one here and update the team/project details accordingly.
