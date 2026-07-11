# GSE Operations Management System

Web system that replaces the Excel-based GSE daily reports. Phase 1 covers the
Dashboard, Equipment Master, Location Master, the Allocate / Transfer / Return
workflow with approvals, and Compliance (PM, TUV, Ramp Pass, Insurance).

## Run it locally

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py seed_demo      # 140 units of realistic demo data
venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in:

| User | Password | Role |
|---|---|---|
| `admin` | `gse2026` | Full access + Django admin |
| `supervisor` | `gse2026` | Raises allocation requests |

`seed_demo` wipes and reloads the demo fleet, so don't run it against real data.

## What's built

**Dashboard** — Total / Available / Allocated / Maintenance / OOS / Expired /
Upcoming Expiry, daily operational readiness %, location-wise availability, a
14-day readiness trend, pending approvals and an expiry watchlist. Every tile is
clickable and every filter (Location, Department, Equipment Type, Status, free-text
search) flows through to the tables and the CSV export.

**Equipment Master** — unique Equipment Number, type, model, manufacturer, serial,
registration, purchase date, location, department, status, photo and documents.
Full movement history per unit. CSV export honours the active filter.

**Location Master** — Location / Station / Area / Zone / Department / Responsible
Supervisor, with per-location readiness.

**Allocation** — Allocate, Transfer and Return requests carry From, To, date, time,
Allocated By, Approved By and Reason. Nothing moves until a supervisor approves;
approval is what actually relocates the unit and flips its status. A Return closes
the open allocation it settles.

**Compliance** — PM Due, TUV, Ramp Pass and Insurance dates drive status
automatically. Expired and expiring-within-30-days are listed separately, along
with employee training that has lapsed or is about to.

## Two rules worth knowing

1. **Compliance never overrides a supervisor.** Maintenance, OOS and Scrap are set
   by a human and the nightly compliance job leaves them alone — a lapsed TUV must
   never silently pull a unit out of the workshop or un-scrap it. Available and
   Allocated *are* recalculated, and renewing a certificate puts the unit back
   where it belongs (Allocated if it's still out on loan, otherwise Available).

2. **You cannot allocate a non-compliant unit.** Any attempt to put a unit with an
   expired TUV / Ramp Pass / PM onto the ramp is rejected at the form, naming the
   certificate that lapsed. Same for units in Maintenance, OOS or Scrap.

## Nightly job

```
0 1 * * *  cd /srv/gse && venv/bin/python manage.py refresh_compliance
```

Re-evaluates every unit against its compliance dates and stores the day's readiness
snapshot (which is what the trend chart plots).

## Tests

```bash
venv/bin/python manage.py test ops
```

13 tests covering the compliance engine, the allocation rules and the dashboard
aggregation.

## Notes on production

SQLite is used for the prototype. The models are database-agnostic — moving to
PostgreSQL is a change to `DATABASES` in `gse_ops/settings.py` and nothing else.
Set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0` and `CSRF_TRUSTED_ORIGINS` from the
environment before deploying.
