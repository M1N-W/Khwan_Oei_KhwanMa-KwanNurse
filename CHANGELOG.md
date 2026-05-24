# Changelog

All notable changes to this project are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `ARCHITECTURE.md` — Canonical system design document
- `CHANGELOG.md` — Version history

## [1.0.0] — 2026-05-18

### Added
- LINE chatbot with 6 patient features (symptom assessment, personal risk, appointment, health knowledge, auto-follow-up, nurse consultation)
- Nurse dashboard (`/dashboard/*`) with Jinja2 + HTMX
- Production auth (bcrypt + CSRF + rate limit + idle timeout + password policy)
- APScheduler with single-owner pattern
- TTL cache and batch update patterns
- Optional Gemini LLM with fallback and daily budget guard
- Docker deployment files (`Dockerfile`, `docker-compose.yml`)

## Types
- `feat` — New feature
- `fix` — Bug fix
- `refactor` — Code change that neither fixes a bug nor adds a feature
- `docs` — Documentation only changes
- `test` — Adding missing tests or correcting existing tests
- `chore` — Build process or auxiliary tool changes
