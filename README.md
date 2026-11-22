# Restaurant management system (Alpha)

QR-based ordering for restaurants. A guest scans the code on a table, orders from their phone, and the staff and kitchen screens update live. Python (Django + Channels), SQLite, plain JavaScript.

A complete QR dining platform you can sell: restaurants sign up for a free trial, pay for a plan, and run ordering, kitchen, floor, bookings, stock, team and reports from one place. You run every restaurant from the platform console. See "Still to come" for what is not built yet.

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

## Selling it: signup, plans and your platform console

- **Sign-up:** restaurants sign themselves up at `/signup/` (also the "Start a free trial" button on the home page). They get a 14-day free trial of Pro, their tables and QR codes, and a setup checklist on their overview.
- **Plans** (in `core/models.py`, `PLANS`): Starter Rs 4,999 (10 tables, 5 staff), Pro Rs 9,999 (40 tables, 25 staff), Enterprise Rs 24,999 (no limits). Limits apply when adding tables or staff. When a trial or paid period ends, or you pause a restaurant, guests cannot order or book; staff see a notice.
- **Platform console** at `/platform/`, for superusers only (the demo creates `platform` with the demo password). See every restaurant, its status, plan, usage, 30-day orders and sales; change plan, **Mark paid** (records a bank or cash payment and extends the period), extend a trial, pause or resume. Monthly revenue and payments collected are at the top.
- **Online payment:** not connected yet. Plan payments go through one function, `services.record_plan_payment`; a payment gateway calls the same function when it is added, so renewals become automatic.

## Brand, bookings, stock and languages

- **Settings and plan** (owner): logo, brand colour (used across the guest phone and staff screens), tagline, currency, service charge, tax, guest WiFi, rewards; current plan and usage; the restaurant's booking link.
- **Reservations:** guests book at `/book/<restaurant>/` (link on the Settings page). Bookings arrive live on **Reservations** for waiters and owners: confirm (emails the guest if they gave an email), seat at a table, cancel or mark no-show.
- **Inventory** (owner): track ingredients with a warning level; give each dish a recipe in Menu and tables. Stock is taken when an order goes to the kitchen and returned if it is cancelled. A dish sells out by itself when an ingredient cannot cover one more portion and comes back on restock (unless someone marked it sold out by hand). Low stock shows on the kitchen display and the overview.
- **Urdu:** guests switch the phone between English and اردو (top right); Urdu is right to left with a Nastaliq font. The choice is remembered on that phone. Dish names, descriptions, quiz questions and messages from the server stay as written.

## Running the floor (Beta 2)

- **Sold out:** on the service desk, open **Menu** and tap a dish. Guests' phones grey it out at once and it cannot be ordered; tap again when it is back.
- **Change an order:** on a ticket, the × next to a dish removes it, **Cancel** cancels the whole order (it comes off the bill and the kitchen board). Allowed until the order is served and while nobody has started paying. A guest can cancel their own order until the waiter sends it to the kitchen. Cancelled orders stay in the admin with who cancelled them.
- **Team:** the owner adds waiters, kitchen staff and other owners at **Team**, changes roles, resets passwords and switches logins off. Switching off or resetting signs that person out everywhere. Passwords need 8+ characters and cannot be common or only numbers.
- **Menu and tables:** the admin shows an owner only their own restaurant: dishes, tables, deals, quiz, orders and bills. Team logins are managed on the Team page, not in the admin.

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

A payment gateway (guest bills and plan renewals), Urdu for staff screens and server messages, translated dish names, reserved tables on the floor map. Known shortcuts, each marked `shortcut:` in the code:

- Uploaded photos are served by Django from the disk: fine for a few restaurants, move to S3 or a CDN at scale.
- Guests at a table share the order list but not a cart.

Design: `docs/superpowers/specs/2026-11-20-restaurant-alpha-design.md`.

## Design prototype (all Beta screens)

`prototype/index.html` is a clickable, front-end-only prototype of the full Beta product: guest QR ordering, staff tablet, kitchen display, owner dashboard and platform console, with sample data, no backend and no validation. Open the file in a browser, or serve the folder with `python -m http.server -d prototype`.
