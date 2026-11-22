# Restaurant management system (Alpha)

QR-based ordering for restaurants. A guest scans the code on a table, orders from their phone, and the staff and kitchen screens update live. Python (Django + Channels), SQLite, plain JavaScript.

Beta 1 = the full Alpha flow plus a production setup (Postgres, Redis, real email, HTTPS-only settings, login lockout, dish photo uploads). Still one restaurant per deployment; see "Still to come".

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
| `sara` | `/staff/` | a second waiter, for assigning requests |
| `kitchen` | `/kitchen/` | mark dishes ready by station |

Guests need no login. Each table's link is printed by `seed_demo` and shown, with a QR code, at `/staff/qr/`.

## What it does now

1. **Scan and order.** Each table has its own QR code. Guests give their name, so two people at one table share one order list and one bill. The menu shows spice level, allergens, sizes and deals (for example a lassi for Rs 99 with a curry).
2. **Staff and kitchen.** The waiter sends the order to the kitchen with one tap. The kitchen board shows live timers by station and turns red past the dish's prep time.
3. **While they wait.** Guests see a countdown, the restaurant WiFi password with a copy button, reward points (they join with an email to keep them) and a Spice Quiz where tables compete live.
4. **Call the waiter.** Water, napkins, bill or a waiter. The waiter can assign it and the guest sees who is coming and how fast they answered.
5. **Live floor map.** Every table's status (free, ordering, eating, bill, help), who is sitting there and what they ordered.
6. **Bill.** Itemised, with service charge and tax, split between guests, a tip, and a payment choice: cash, bank transfer, card, Apple Pay or Google Pay. The waiter confirms at the table. No money moves through the app.
7. **Invoice and review.** The invoice is emailed, reward points are added, and the guest can leave a star review.
8. **Owner overview** at `/owner/`: tables in use, orders and sales today, sales by day, peak hours, best sellers, live floor and reviews.

Set the restaurant's WiFi, service charge, tax and reward in the admin (Restaurants). Invoices print in the server terminal until you set `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and `DEFAULT_FROM_EMAIL`.

If you ran an older version, delete `db.sqlite3`, then run `migrate` and `seed_demo` again.

## Look and feel

One dark, neutral operations theme: Inter type, a single blue accent, and colour only where it means something (green live or good, amber attention, red late). Staff pages (service desk, kitchen display, overview, QR codes) share a left navigation rail that becomes a top bar on narrow screens. Each dish has an illustration (`core/static/core/dishes/`). In the admin, set a dish's **Art** to pick its illustration and tick **Featured** to show it as the Chef's pick. A dish with no art gets an initial. Fonts load from Google Fonts, so the first view needs an internet connection; without it the app falls back to system fonts.

## Put it online (Render)

`render.yaml` sets up the app, a Postgres database and Redis for live updates, all on Render's **free** plans.

1. Push this branch to GitHub. In the Render dashboard choose **New**, then **Blueprint**, pick this repository and branch. Render reads `render.yaml` and shows what it will create. Approve it.
2. Free plan limits: the app sleeps after 15 idle minutes (the next visit takes about a minute to wake it), the free database expires after 30 days (Render emails you first), and uploaded dish photos are lost when the app restarts. For a paying restaurant, switch to the paid plans noted at the top of `render.yaml`.
3. When it is live, open the `onrender.com` URL. The demo restaurant is created on the first start (`SEED_DEMO=1`); its password is the `DEMO_PASSWORD` value in the service's Environment tab. Set `SEED_DEMO` to `0` once you have a real restaurant.
4. Real invoice emails: in the Environment tab fill `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and `DEFAULT_FROM_EMAIL` (for example a Gmail app password with `smtp.gmail.com`, or Brevo/SendGrid SMTP).
5. Own domain: add it in Render, then add it to `ALLOWED_HOSTS` (comma separated).

Production settings switch on automatically when `DEBUG=0`: only listed hosts are accepted, cookies are HTTPS only, HTTP redirects to HTTPS, HSTS is on. Five wrong passwords for one username lock it for 15 minutes. `/healthz/` answers `ok` for the host's health check.

On your own computer nothing changes: without `DATABASE_URL` and `REDIS_URL` it uses SQLite and in-memory updates. After pulling this version run `pip install -r requirements.txt` and `python manage.py migrate` once.

## Dish photos

In the admin, open a dish and upload a **Photo** (square works best, up to 5 MB). It replaces the illustration on the menu, kitchen tickets and the owner overview.

## Tests

```bash
python manage.py test
```

Covers prices computed on the server, status rules and roles, one restaurant never seeing another's data, and live websocket pushes.

## Still to come (see the SOW)

Beta 2: sold-out toggle, order edits and cancels, staff management, admin scoped per restaurant. Beta 3: restaurant signup, platform console, branding, plans. Beta 4: reservations, inventory, languages, payment gateway. Known shortcuts, each marked `shortcut:` in the code:

- Admin is not scoped per restaurant: fine for one restaurant, fix before a second one.
- Uploaded photos are served by Django from the disk: fine for a few restaurants, move to S3 or a CDN at scale.
- Guests at a table share the order list but not a cart.

Design: `docs/superpowers/specs/2026-11-20-restaurant-alpha-design.md`.

## Design prototype (all Beta screens)

`prototype/index.html` is a clickable, front-end-only prototype of the full Beta product: guest QR ordering, staff tablet, kitchen display, owner dashboard and platform console, with sample data, no backend and no validation. Open the file in a browser, or serve the folder with `python -m http.server -d prototype`.
