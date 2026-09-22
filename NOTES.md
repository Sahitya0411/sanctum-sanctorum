# NOTES.md — Sanctum Sanctorum Bookstore

## Live URL

**https://sanctum-sanctorum-qx9d.onrender.com**

- `GET /health` → `{"status": "ok"}`
- `GET /books` → returns 12 seeded books
- Frontend UI → `https://sanctum-sanctorum-qx9d.onrender.com/`
- API docs → `https://sanctum-sanctorum-qx9d.onrender.com/docs`

> **Note:** Hosted on Render free tier (Postgres database). The instance may spin down after inactivity — first request after idle can take ~30 seconds to wake up.
> Seeded member you can use: any member created via `POST /members` or use the demo data seeded at startup.


---

## What I Finished

All five feature areas are complete and the full test suite passes (`uv run pytest` → 201/201):

| Feature | Status |
|---|---|
| Books — CRUD, validation, ISBN checksum, filtering, sorting, pagination, PATCH | ✅ Complete |
| Members — registration, email dedup, tier access rules, stats | ✅ Complete |
| Orders — pricing, tier + bulk discounts, all-or-nothing stock reservation, pay/cancel | ✅ Complete |
| Loans — borrow rules (6 checks in spec order), returns, late fees, status, member loan list | ✅ Complete |
| Reports — top-books by paid-order quantity | ✅ Complete |

---

## Architectural Decisions & Trade-offs

### Layer separation
All business rules live in `app/services/`. Routers are deliberately thin: parse input, call the service, return the result. No business logic leaked into routers or schemas.

### ISBN-13 checksum
Implemented in `normalize_isbn13` inside `schemas.py` so that rejection happens at schema-validation time (→ 422 before any DB call). The algorithm: weight digits 1,3,1,3… and verify the total is divisible by 10.

### `tier_at_least` fix
The original implementation used `>` instead of `>=`, which meant `master` couldn't access restricted books. Changed to `>=` so the minimum tier (`master`) and above are permitted — matching the spec ("tier below master → 403").

### Loan status is computed at read time
`loan_status` and `to_loan_out` are pure functions that take `now` as a parameter. No `status` column is stored — it is derived from `returned_at`, `due_at`, and the current clock. This keeps the DB clean and consistent with a controlled clock in tests.

### `total` in book list
Changed from `len(books)` (which was counting only the page) to a `COUNT(*)` subquery executed before pagination. This correctly returns the number of matching rows regardless of `limit`/`offset`.

### All-or-nothing stock check in orders
Before decrementing any stock, the service validates _every_ item against current stock. Only then does it modify stock. This satisfies the spec's "all-or-nothing" requirement without needing a transaction savepoint.

### Email deduplication
`func.lower()` on both sides in the SELECT ensures case-insensitive uniqueness even though SQLite's `UNIQUE` constraint is already case-insensitive for ASCII (the belt-and-suspenders approach also works on Postgres where it is not).

### Loan `due_at` boundary
The spec says "at exactly `due_at` the loan is still active." The `loan_status` function uses strict `>` for "overdue", matching the spec text and the test `test_loan_due_right_now_does_not_block_borrowing`.

### Late fee calculation
`math.ceil` is used so any partial second past midnight counts as a full day, as specified. Fee is capped at the book's current price at return time.

---

## What I Would Do With More Time

- **Concurrent order safety**: two simultaneous requests for the last copy of a book could both pass the stock check. A `SELECT FOR UPDATE` or an optimistic-lock version column would prevent this.
- **`GET /members` with pagination**: the optional endpoint mentioned in ASSIGNMENT.md.
- **Deployment**: set up Railway (or Render) with Postgres, change `SANCTUM_DATABASE_URL`, and verify the app starts cleanly on the hosted DB.
- **More edge-case tests**: e.g. placing an order for quantity 9 (no bulk discount) vs 10 (bulk discount applies).

---

## Spec Ambiguities / Notes

- **PATCH ignores unknown fields** — the spec says "Unknown fields are ignored too." `BookUpdate` (already in the repo) is an open Pydantic model that drops extra keys, so this is handled correctly.
- **`isbn` silently ignored in PATCH** — `BookUpdate` does not include `isbn` as a field at all, so even if the client sends it, Pydantic will ignore it. No special handling needed.
- **Mixed-case title sort** — the spec explicitly says "unspecified." SQLite sorts uppercase before lowercase by default; the test suite accepts both orderings.

---

## AI Usage

**Tools used**: Google Gemini (via Antigravity IDE) for the full implementation session.

**What I used it for**:
- Reading all files in parallel and synthesizing the gap analysis (which stubs needed what logic).
- Writing all service implementations and schema validators.
- Catching the `tier_at_least` off-by-one bug that was already in the original code.

**Where the AI was wrong / needed correction**:
- The first attempt at `list_books` placed the `total` count _after_ pagination (using `len(books)`), which would have given wrong totals. I caught this from the test `test_total_counts_filtered_results_before_pagination` and switched to a `COUNT(*)` subquery executed on the filtered query _before_ the `LIMIT/OFFSET` is applied.
- The AI initially suggested importing `Book` at the module level in `orders.py` — I moved the import inside the function body to avoid any potential circular import at startup (the service imports from members which doesn't import books; keeping it local is cleaner).
