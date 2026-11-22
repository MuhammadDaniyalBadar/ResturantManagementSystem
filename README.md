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

## Design prototype (all Beta screens)

`prototype/index.html` is a clickable, front-end-only prototype of the full Beta product: guest QR ordering, staff tablet, kitchen display, owner dashboard and platform console, with sample data, no backend and no validation. Open the file in a browser, or serve the folder with `python -m http.server -d prototype`.
