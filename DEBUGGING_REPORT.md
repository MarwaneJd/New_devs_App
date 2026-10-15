# Property Revenue Dashboard: investigation and fixes

The existing FastAPI, SQLAlchemy/Postgres, Redis, and React application was debugged in place. No replacement system was built. Both supplied accounts were used through the login API and browser UI before and after the fixes.

## Reproduction before the fixes

With the supplied seed data and credentials:

| Request | Original result | Correct result |
| --- | --- | --- |
| Sunset, `prop-001` | USD 1,000.00, 3 bookings | USD 2,250.00, 4 bookings |
| Ocean, `prop-001`, after Sunset warmed the cache | USD 1,000.00, 3 bookings | USD 0.00, 0 bookings |
| Sunset, Ocean's `prop-004` | USD 1,776.50, 4 bookings | 404 |
| Monthly calculation helper, March 2024 | Always zero | USD 2,250.00 for Sunset's `prop-001` |

Both browser sessions also displayed the same hard-coded property dropdown: Ocean saw Sunset's “Beach House Alpha” instead of its own “Mountain Lodge Beta.”

## Root causes and changes

### Tenant isolation and cache collisions

`backend/app/services/cache.py` used `revenue:{property_id}`. Property IDs are only unique within a tenant; the seed intentionally gives both tenants `prop-001`. The first caller's result could be served to the other tenant.

The cache now uses a versioned JSON tuple containing tenant, property, year, month, and currency. Old unsafe keys are ignored. Cache payload identity is checked before reuse. Redis errors and malformed entries trigger a real database calculation. Failed database calculations are never cached.

The dashboard requires an authenticated tenant, and all property/reservation SQL scopes by both tenant and property. Unknown and foreign properties return the same 404. The dropdown now loads from `/api/v1/dashboard/properties`, scoped to the authenticated tenant. Frontend caches clear when the session token changes; stale component requests cannot replace a newer selection's data. Debug tenant headers no longer appear in the frontend's revenue calls.

### Database errors were disguised as successful reports

`DatabasePool` referenced nonexistent `supabase_db_*` settings instead of `DATABASE_URL`, selected the synchronous `QueuePool` for an async engine, and declared `get_session()` as async while using it directly with `async with`. The revenue service also constructed a new pool on every request.

The exception handler then substituted hard-coded totals, including financial figures for properties the caller did not own. These figures appeared to be successful reports and were cached.

The existing pool now uses the configured Postgres URL, the async driver's pool, an idempotent initialization lock, a synchronous session factory accessor, and the shared pool instance. Its lifecycle is managed by the application. `sqlalchemy[asyncio]` supplies the required `greenlet` dependency on the tested ARM environment; the uv lockfile is updated. Database failures return 503 instead of invented revenue.

### Calendar months and years were not implemented

`calculate_monthly_revenue()` was an unused zero-returning placeholder. Its draft query used naive date boundaries, and the API/frontend did not send reporting periods.

The existing aggregation now supports all-time, annual, and monthly reports. Calendar boundaries are constructed in the property's IANA time zone and converted independently to UTC. The query includes the start and excludes the next period's start. This handles DST, leap years, and December rollover.

For Paris, March 2024 runs from **February 29 at 23:00 UTC** through **March 31 at 22:00 UTC**, exclusive. The seeded USD 1,250 booking at February 29, 23:30 UTC therefore belongs in March. The other three bookings total exactly USD 1,000, giving **USD 2,250.00 and four bookings**.

### Precision, currency, and misleading display

The API converted the exact decimal sum to `float`; the frontend then applied `Math.round(amount * 100) / 100`. A value such as 1.005 can consequently display as 1.00. The seeded 333.333 + 333.333 + 333.334 also demonstrates why rounding each booking before summing loses a cent.

Postgres sums `NUMERIC` values and Python retains `Decimal`. The report rounds once, at the end, using `ROUND_HALF_UP` to two decimal places. The API returns a decimal string such as `"2250.00"`, and the frontend formats that string without numeric conversion. This is an intentional API type change, applied to its consumer.

The original service also labeled every result USD and summed without checking currency. Results now use the actual reservation currency. Mixed-currency reports require a currency selection, which the dropdown supplies; amounts in different currencies are never added or converted. The fabricated 12% trend badge was removed.

### Authentication paths that could undermine isolation

During investigation, the fallback login path was found to list users without verifying passwords, unknown users inherited `tenant-a`, and fallback JWT parsing disabled signature verification. These paths could bypass the dashboard's intended isolation.

The provided challenge accounts remain available. Other logins must verify credentials with the configured identity provider. Authorization uses verified server-controlled tenant claims, never user-editable metadata or a default tenant. Missing tenants fail closed. Invalid, expired, non-expiring, and old static mock tokens are rejected, and authentication caching cannot extend a token's expiration.

## Verification

Start with the assignment's normal command:

```bash
docker compose up --build -d
```

Port 3000 was already occupied on this machine, so the verified frontend is running at **http://localhost:3001**. The backend remains at **http://localhost:8000/docs**. To reproduce this port choice:

```bash
FRONTEND_PORT=3001 docker compose up --build -d
```

Run the regression suite and the read-only live check from the repository root:

```bash
docker compose exec -T backend python -m unittest discover -s tests -v
docker compose exec -T backend python scripts/verify_dashboard.py
```

The regression suite passed **31 tests**. It covers real Postgres aggregation, Paris/New York DST boundaries, exclusive period ends, local-year rollover, sub-cent sums, half-cent rounding/refunds, duplicate property IDs, empty and foreign properties, multiple currencies, both cache warming orders, cache filter isolation, malformed/failed Redis, database failures, credentials, tenant claims, and token validation. Database fixtures are rolled back.

The live check signs in using both provided credentials, verifies all six seeded tenant/property combinations for all-time/March 2024/annual 2024 reports, checks foreign-property rejection and tenant-scoped dropdowns, and performs 20 concurrent alternating-client refreshes. It passed.

| Tenant | Property | All-time / March 2024 / annual 2024 | Bookings |
| --- | --- | ---: | ---: |
| Sunset | Beach House Alpha (`prop-001`) | USD 2,250.00 | 4 |
| Sunset | City Apartment Downtown (`prop-002`) | USD 4,975.50 | 4 |
| Sunset | Country Villa Estate (`prop-003`) | USD 6,100.50 | 2 |
| Ocean | Mountain Lodge Beta (`prop-001`) | USD 0.00 | 0 |
| Ocean | Lakeside Cottage (`prop-004`) | USD 1,776.50 | 4 |
| Ocean | Urban Loft Modern (`prop-005`) | USD 3,256.00 | 3 |

The production Docker frontend build passed, as did ESLint for both changed dashboard components and `git diff --check`. The wider TypeScript check has **190 existing diagnostics**; comparison against an untouched `HEAD` source tree produced the same 190 diagnostics and **no new diagnostics**. The assignment's unrelated incomplete screens were left outside this debugging work.

Browser verification covered both accounts, correct property names/totals, March filtering, annual filtering, and refresh under Ocean's account. [Saved Sunset March screenshot](docs/sunset-march-2024.jpg).

## Reporting decisions

- Revenue is attributed in full to the reservation's local check-in month, matching the original query's intent. It is not prorated across nights.
- Reporting uses two decimal places and final-sum half-up rounding, matching the supplied cent-based financial data. FX conversion and currency-specific minor-unit rules are outside this assignment implementation.
- Existing five-minute cache freshness is retained. New reservation-write invalidation was not added because the assignment does not include a reservation-writing path.
- A failed database query returns 503 on a cache miss; an already cached result can still be served until its normal TTL expires.

## Video deliverable

[LOOM_WALKTHROUGH.md](LOOM_WALKTHROUGH.md) contains the timed narration and demo steps. The recorded Loom video link must be included with the repository link in the assignment submission.
