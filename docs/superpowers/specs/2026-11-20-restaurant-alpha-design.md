# Restaurant system, Alpha: design

Source of requirements: the SOW doc (FR numbers below refer to it). This file covers only what Alpha builds.

## Intent
Prove the core loop works: guest scans a table QR, orders, the kitchen sees it live, the guest sees status, and can call a waiter. For one restaurant, for demos and testing. Not for a live restaurant yet.

## Goals
- FR-01 to FR-11, FR-15 to FR-17, FR-20 to FR-24, FR-27, FR-28, FR-33 to FR-35, FR-53, FR-54.
- Acceptance test: SOW section 10, steps 1 to 8.

## Non-goals (Beta or later)
Payments and bills, floor map, sold-out toggle, order edits, reviews, reports, branding, multi-restaurant onboarding, PWA install, WiFi/rewards/quiz.

## Anti-goals
- A demo that looks right but loses orders on refresh.
- Business rules (prices, status transitions) decided in JavaScript. Python decides; JS only displays.
- Any data readable across restaurants.

## Approach (my call, as agreed in conversation)
Django + HTMX, Django Channels for live updates, SQLite locally (Postgres-compatible models, no raw SQL), in a new folder `restaurant/` beside the untouched `index.html` prototype. Alternatives considered: Next.js full-stack, and Supabase/Firebase. Python was chosen because the owner prefers it and Django's admin panel covers menu and restaurant setup for free.

## Design
- Models (every one has `restaurant`): Restaurant, Table (random `code` for the QR URL), Category, Dish (station, prep minutes, allergens, spice), DishSize, Order (status: placed, cooking, ready, served), OrderItem, ServiceRequest (water, napkins, bill, call; open, acknowledged, done).
- Restaurant scoping: staff users belong to one restaurant; every staff query filters by it. Guests are scoped by the table `code`.
- Screens: guest `/t/<code>/` (menu, cart, status, call); staff `/staff/` (incoming orders, requests); kitchen `/kitchen/` (tickets by station with timers). Owner manages menu and tables in Django admin.
- Live updates: one Channels group per restaurant for staff and kitchen, one per table for the guest. The server saves first, then broadcasts (no lost orders).
- Status changes go through one function that validates the transition, then broadcasts.
- Guest cart is held in the browser until Place order; the server computes all prices.

## Left to the builder
Exact templates and styling, QR generation library, demo seed data.

## Test plan
Automated tests for: order creation and price calculation, valid and invalid status transitions, service request flow, cross-restaurant isolation, and a websocket broadcast on order placement. Then the manual acceptance test from SOW section 10.
