# Authentication Setup

## Overview
- The FastAPI app exposes `/auth/register`, `/auth/login`, `/auth/me`, `/auth/admin/ping`, and a placeholder `/auth/signicat/initiate` route.
- Users are stored in a relational database through SQLAlchemy. Postgres is the primary target; during local development the app falls back to SQLite (`data/local.db`) if `DATABASE_URL` is missing.
- Passwords are hashed with `bcrypt`, and authentication uses JWT (HS256). The API returns an `access_token` that the frontend can keep in an `httpOnly` cookie.

## Environment variables
- `DATABASE_URL`: e.g. `postgresql+asyncpg://user:password@host:5432/database`. Required in staging/production.
- `AUTH_SECRET_KEY`: secret for JWT signing. Must be set everywhere except isolated local development. Store it in AWS Secrets Manager or SSM.
- `ACCESS_TOKEN_EXPIRE_MINUTES`: optional override (default 30).
- `SQLALCHEMY_ECHO`: set to `1` or `true` to log SQL for debugging.

## Prepare AWS RDS Postgres
- Create a Postgres instance inside the VPC that hosts the backend.
- Open the security group so the API service can reach port 5432 (or use a private link/tunnel if the API runs outside AWS).
- Provision a database and user for Techdom, e.g. `CREATE DATABASE techdom;` and `CREATE USER techdom_user WITH PASSWORD '...';`.
- Configure `DATABASE_URL` as `postgresql+asyncpg://techdom_user:<password>@<host>:5432/techdom` in the API environment.

## Tables and first admin user
- `init_models()` runs on FastAPI startup and creates the `users` table if it is missing. No migrations are required for this initial schema.
- To create the first admin (useful before BankID is integrated), run `python scripts/create_admin_user.py --email admin@example.com --password TempPass123`. The script honours the same `.env` / environment variables as the API.
- You can deactivate or rotate that admin once BankID handles privileged access.

## Signicat BankID placeholder
- `GET /auth/signicat/initiate` currently returns `{status: "not_implemented"}`.
- Next steps: obtain Signicat credentials, register redirect URLs (for example `https://api.yourdomain.com/auth/signicat/callback`), then wire the OIDC flow into a dedicated route. The admin-only guard `GET /auth/admin/ping` is ready to secure BankID-only flows.

## Frontend integration hints
- `POST /auth/login` expects JSON `{ "email": "...", "password": "..." }` and responds with `{ access_token, token_type, user }`.
- Store the `access_token` in an `httpOnly` cookie (recommended via a Next.js route handler) and send it as `Authorization: Bearer <token>` for subsequent API calls.
- `GET /auth/me` returns the current user. On `401`, clear the cookie and show the login modal.
- Use `GET /auth/admin/ping` to check whether an authenticated user has the admin role.

## Local development quick start
- Install dependencies: `pip install -r requirements-dev.txt` (new packages: SQLAlchemy, asyncpg, passlib, python-jose).
- Without `DATABASE_URL`, the service creates `data/local.db` automatically. Remove the file if you need a clean start.
- Run the API: `uvicorn apps.api.main:app --reload`, then test with `curl` or `httpie` against `/auth/register` and `/auth/login`.
