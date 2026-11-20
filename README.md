# Restaurant management system (Alpha)

QR-based ordering for restaurants. A guest scans the code on a table, orders from their phone, and the staff and kitchen screens update live. Python (Django + Channels), SQLite, plain JavaScript.

Alpha = one restaurant, for demos and testing. Not ready for a live restaurant yet (see "Not in Alpha").

## Run it on your computer

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo      # demo restaurant, 6 tables, 3 logins; prints each table's guest link
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

Open http://127.0.0.1:8000 . Logins (password `demo1234`, or `$DEMO_PASSWORD`):

| User | Goes to | Can do |
| --- | --- | --- |
| `owner` | `/staff/`, `/kitchen/`, `/staff/qr/`, `/admin/` | everything, edits menu and tables in the admin |
| `waiter` | `/staff/` | confirm orders, serve, answer requests |
| `kitchen` | `/kitchen/` | mark dishes ready by station |

Guests need no login. Each table's link is printed by `seed_demo` and shown, with a QR code, at `/staff/qr/`.

## Get a public link (Render)

1. Render dashboard, New, Blueprint, pick this repository and the `alpha` branch. It reads `render.yaml`.
2. When the deploy is live, open the URL. The demo password is in the service's `DEMO_PASSWORD` environment variable.
3. The free plan keeps SQLite inside the container, so data resets on each deploy and sleeps when idle. Fine for demos only.

## Tests

```bash
python manage.py test
```

Covers prices computed on the server, status rules and roles, one restaurant never seeing another's data, and live websocket pushes.

## Not in Alpha (see the SOW)

Payments and bills, floor map, sold-out toggle, order edits, reviews, reports, branding, multi-restaurant onboarding, PWA install. Known shortcuts, each marked `shortcut:` in the code:

- In-memory channel layer: one server process only. Use Redis before running more.
- Admin is not scoped per restaurant: fine for one restaurant, fix before a second one.
- Dish photos are links, not uploads.
- All hosts allowed: set `ALLOWED_HOSTS` before a real customer uses it.
- Guests at a table share the order list but not a cart.

Design: `docs/superpowers/specs/2026-11-20-restaurant-alpha-design.md`.
