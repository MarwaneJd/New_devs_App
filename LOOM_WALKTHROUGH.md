# Loom walkthrough — approximately 5 minutes

Read the quoted narration aloud. Italic lines are screen directions and should not be read aloud. Allow time for the on-screen demonstrations.

## 0:00–0:25 — Investigation

*Show the dashboard.*

“I investigated the dashboard using both supplied client accounts. Before the fixes, Sunset and Ocean received the same revenue for property ID prop-001, despite belonging to different companies. Sunset could also request Ocean’s property, and both accounts saw the same property dropdown. I traced these issues through the existing code.”

## 0:25–1:15 — Tenant isolation

*Show `backend/app/services/cache.py`, then the tenant filters in `reservations.py`.*

“The Redis cache key contained only the property ID. However, property IDs are unique within each tenant, and both companies have a prop-001. One client’s cached result could therefore be returned to another client.

“The corrected key includes the authenticated tenant, property, reporting period, and currency. It is versioned so old unsafe entries cannot be reused, and cached identities are checked.

“Database queries now require both tenant and property IDs. Foreign properties return 404, the dropdown loads only the authenticated tenant’s properties, and changing accounts clears the frontend cache.”

## 1:15–1:55 — Database failures

*Show `backend/app/core/database_pool.py` and the removed mock fallback in the Git diff.*

“The database pool referenced missing settings, used a synchronous pool with an async driver, and returned a coroutine where a session was expected. Each calculation also created another pool.

“When this failed, the revenue service substituted hard-coded totals.

“I repaired the existing pool to use DATABASE_URL, the async driver, and a shared session factory. Database failures now return 503 instead of fabricated financial results.”

## 1:55–2:40 — Monthly reporting and time zones

*Show `revenue_bounds()` and the February 29 booking in `database/seed.sql`.*

“Monthly revenue was a placeholder returning zero, and its draft boundaries used naive timestamps.

“The seed contains a Paris check-in at February 29, 23:30 UTC. That is already March 1 locally, so its 1,250 dollars belongs in March.

“The fix constructs calendar boundaries in the property’s time zone, converts each boundary to UTC, and excludes the next period’s start. This handles daylight saving changes and year rollover. Sunset’s corrected March total is 2,250 dollars across four bookings.”

## 2:40–3:25 — Precision and safeguards

*Show the final Decimal calculation and string formatting in `RevenueSummary.tsx`.*

“The API converted exact decimals to floats, and the frontend rounded them again. This can incorrectly round 1.005 down to 1.00.

“Now the database sums exact amounts, Python rounds the final Decimal once using half-up rounding, and the API returns a decimal string. The frontend formats it without floating-point conversion.

“Currencies remain separate. I also removed the unsupported trend percentage and closed authentication fallbacks involving unchecked passwords, default tenant access, and unverified tokens.”

## 3:25–4:25 — Demonstrate both accounts

*Show Sunset’s March report, switch to Ocean, then refresh.*

“Sunset now shows Beach House Alpha at 2,250 dollars with four March bookings.

“For Ocean, prop-001 identifies Mountain Lodge Beta. Its zero total is correct because the seed contains no reservations for that tenant and property.

“Ocean’s Lakeside Cottage shows 1,776 dollars and 50 cents across four March bookings. Each dropdown contains only that client’s properties, and refreshing preserves the correct account.”

## 4:25–5:00 — Verification

*Run these commands:*

```bash
docker compose exec -T backend python -m unittest discover -s tests -v
docker compose exec -T backend python scripts/verify_dashboard.py
```

“The regression suite passes thirty-one tests covering database calculations, time-zone boundaries, rounding, currency separation, tenant isolation, and failure handling.

“The live check uses both supplied accounts, verifies all six property entries, rejects foreign-property requests, and performs twenty concurrent refreshes. The production build and dashboard component lint also pass.”
