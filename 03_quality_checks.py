"""
MELIA HOTELS — Data Quality Checks (v2)
========================================
Run after schema.py + data.py.

Categories:
  1. Row counts          (static and synthetic-with-tolerance)
  2. Null checks         (required fields populated)
  3. Duplicate detection
  4. Foreign-key integrity
  5. Value domains       (enum coverage; CHECKs already enforce this but we
                          double-check for visibility)
  6. Business rules      (pricing math, cancellation logic, date ordering)
  7. Trigger correctness (the new bit: verify the AFTER INSERT trigger
                          produced consistent listed_price / total_paid /
                          BookingModifier / Invoice / RoomDay status /
                          MeliaRewards balance)
  8. Completeness        (every hotel has rooms, every room has a calendar,
                          all years covered)
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "melia.db"

conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA foreign_keys = ON")
cur = conn.cursor()

passed = failed = warnings = 0


def check(label, ok, actual=None, expected=None, warn=False):
    global passed, failed, warnings
    status = "✅ PASS" if ok else ("⚠️  WARN" if warn else "❌ FAIL")
    detail = f"  (got {actual}, expected {expected})" if not ok and actual is not None else ""
    print(f"  {status}  {label}{detail}")
    if ok:
        passed += 1
    elif warn:
        warnings += 1
    else:
        failed += 1


def q(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


print("=" * 62)
print("  MELIA HOTELS — Data Quality Report (v2)")
print("=" * 62)

# ── SECTION 1: ROW COUNTS ────────────────────────────────────────
print("\n[1] Row counts")
static_counts = {
    "Brands":        (q("SELECT COUNT(*) FROM Brands"),        5),
    "Hotel":         (q("SELECT COUNT(*) FROM Hotel"),         20),
    "PriceModifier": (q("SELECT COUNT(*) FROM PriceModifier"), 44),
}
for table, (actual, expected) in static_counts.items():
    check(f"{table}: {actual:,} rows", actual == expected, actual, expected)

# Customer and MeliaRewards counts are now generated (not hardcoded)
n_cust = q("SELECT COUNT(*) FROM Customer")
n_direct = q("SELECT COUNT(*) FROM Customer WHERE customer_type = 'direct'")
n_agency = q("SELECT COUNT(*) FROM Customer WHERE customer_type = 'agency'")
check(f"Customer: {n_cust:,} rows (≥19 000)", n_cust >= 19_000, n_cust, "≥19 000")
check(f"  of which direct: {n_direct:,} (≥18 990)", n_direct >= 18_990)
check(f"  of which agency: {n_agency:,} (≥10)", n_agency >= 10)

n_mem = q("SELECT COUNT(*) FROM MeliaRewards")
check(f"MeliaRewards: {n_mem:,} rows (≥2 000)", n_mem >= 2_000, n_mem, "≥2 000")

# Room: 20 original + per-hotel template fills, may vary slightly with seed.
n_rooms = q("SELECT COUNT(*) FROM Room")
check(f"Room: {n_rooms:,} rows (≥800)", n_rooms >= 800)

# RoomDay: rooms × days in calendar
# (1095 days for 2025-01-01 → 2027-12-31; no leap year in this range)
n_rd = q("SELECT COUNT(*) FROM RoomDay")
expected_rd = n_rooms * 1095
check(f"RoomDay: {n_rd:,} rows (= rooms × 1 095)",
      n_rd == expected_rd, n_rd, expected_rd)

# Booking / Invoice — synthetic counts, allow tolerance
n_bookings = q("SELECT COUNT(*) FROM Booking")
n_invoices = q("SELECT COUNT(*) FROM Invoice")
check(f"Booking: {n_bookings:,} rows (≥18 000)", n_bookings >= 18_000)
check(f"Invoice count matches Booking count",   n_bookings == n_invoices, n_invoices, n_bookings)

n_bm = q("SELECT COUNT(*) FROM BookingModifier")
check(f"BookingModifier: {n_bm:,} rows (≥10 000)", n_bm >= 10_000)


# ── SECTION 2: NULL CHECKS ───────────────────────────────────────
print("\n[2] Null checks (required fields)")
null_checks = [
    ("Brands.name",                 "SELECT COUNT(*) FROM Brands       WHERE name IS NULL"),
    ("Hotel.name",                  "SELECT COUNT(*) FROM Hotel        WHERE name IS NULL"),
    ("Hotel.brand_id",              "SELECT COUNT(*) FROM Hotel        WHERE brand_id IS NULL"),
    ("Room.room_type",              "SELECT COUNT(*) FROM Room         WHERE room_type IS NULL"),
    ("Room.base_rate",              "SELECT COUNT(*) FROM Room         WHERE base_rate IS NULL"),
    ("Customer.name",               "SELECT COUNT(*) FROM Customer     WHERE name IS NULL"),
    ("Customer.customer_type",      "SELECT COUNT(*) FROM Customer     WHERE customer_type IS NULL"),
    ("Booking.customer_id",         "SELECT COUNT(*) FROM Booking      WHERE customer_id IS NULL"),
    ("Booking.room_id",             "SELECT COUNT(*) FROM Booking      WHERE room_id IS NULL"),
    ("Booking.listed_price",        "SELECT COUNT(*) FROM Booking      WHERE listed_price IS NULL"),
    ("Booking.total_paid",          "SELECT COUNT(*) FROM Booking      WHERE total_paid IS NULL"),
    ("Invoice.net_amount",          "SELECT COUNT(*) FROM Invoice      WHERE net_amount IS NULL"),
    ("Invoice.total_paid",          "SELECT COUNT(*) FROM Invoice      WHERE total_paid IS NULL"),
    ("Invoice.status",              "SELECT COUNT(*) FROM Invoice      WHERE status IS NULL"),
    ("RoomDay.price_per_day",       "SELECT COUNT(*) FROM RoomDay      WHERE price_per_day IS NULL"),
    ("MeliaRewards.points_balance", "SELECT COUNT(*) FROM MeliaRewards WHERE points_balance IS NULL"),
]
for label, sql in null_checks:
    n = q(sql)
    check(f"{label} has no NULLs", n == 0, n, 0)


# ── SECTION 3: DUPLICATE DETECTION ──────────────────────────────
print("\n[3] Duplicate detection")
dup_checks = [
    ("Customer emails",
     "SELECT COUNT(*) FROM (SELECT email FROM Customer WHERE email IS NOT NULL "
     "GROUP BY email HAVING COUNT(*) > 1)"),
    ("MeliaRewards (one per customer)",
     "SELECT COUNT(*) FROM (SELECT customer_id FROM MeliaRewards "
     "GROUP BY customer_id HAVING COUNT(*) > 1)"),
    ("Invoice (one per booking)",
     "SELECT COUNT(*) FROM (SELECT booking_id FROM Invoice "
     "GROUP BY booking_id HAVING COUNT(*) > 1)"),
    ("RoomDay (room_id, day)",
     "SELECT COUNT(*) FROM (SELECT room_id, day FROM RoomDay "
     "GROUP BY room_id, day HAVING COUNT(*) > 1)"),
    ("Brand names",
     "SELECT COUNT(*) FROM (SELECT name FROM Brands "
     "GROUP BY name HAVING COUNT(*) > 1)"),
]
for label, sql in dup_checks:
    n = q(sql)
    check(f"No duplicates — {label}", n == 0, n, 0)


# ── SECTION 4: FK INTEGRITY ──────────────────────────────────────
print("\n[4] Foreign key integrity")
fk_checks = [
    ("Hotel.brand_id → Brands",
     "SELECT COUNT(*) FROM Hotel WHERE brand_id NOT IN (SELECT brand_id FROM Brands)"),
    ("Room.hotel_id → Hotel",
     "SELECT COUNT(*) FROM Room WHERE hotel_id NOT IN (SELECT hotel_id FROM Hotel)"),
    ("RoomDay.room_id → Room",
     "SELECT COUNT(*) FROM RoomDay WHERE room_id NOT IN (SELECT room_id FROM Room)"),
    ("PriceModifier.hotel_id → Hotel",
     "SELECT COUNT(*) FROM PriceModifier WHERE hotel_id IS NOT NULL "
     "AND hotel_id NOT IN (SELECT hotel_id FROM Hotel)"),
    ("MeliaRewards.customer_id → Customer",
     "SELECT COUNT(*) FROM MeliaRewards WHERE customer_id NOT IN (SELECT customer_id FROM Customer)"),
    ("Booking.customer_id → Customer",
     "SELECT COUNT(*) FROM Booking WHERE customer_id NOT IN (SELECT customer_id FROM Customer)"),
    ("Booking.room_id → Room",
     "SELECT COUNT(*) FROM Booking WHERE room_id NOT IN (SELECT room_id FROM Room)"),
    ("BookingModifier.booking_id → Booking",
     "SELECT COUNT(*) FROM BookingModifier WHERE booking_id NOT IN (SELECT booking_id FROM Booking)"),
    ("BookingModifier.modifier_id → PriceModifier",
     "SELECT COUNT(*) FROM BookingModifier WHERE modifier_id NOT IN (SELECT modifier_id FROM PriceModifier)"),
    ("Invoice.booking_id → Booking",
     "SELECT COUNT(*) FROM Invoice WHERE booking_id NOT IN (SELECT booking_id FROM Booking)"),
]
for label, sql in fk_checks:
    n = q(sql)
    check(label, n == 0, n, 0)


# ── SECTION 5: VALUE DOMAINS ─────────────────────────────────────
# (CHECK constraints already enforce these; the queries here are visibility.)
print("\n[5] Value domains")
domain_checks = [
    ("Customer.customer_type ∈ {direct, agency}",
     "SELECT COUNT(*) FROM Customer WHERE customer_type NOT IN ('direct','agency')"),
    ("Booking.booking_status ∈ {confirmed, cancelled, completed}",
     "SELECT COUNT(*) FROM Booking WHERE booking_status NOT IN ('confirmed','cancelled','completed')"),
    ("Booking.booking_channel ∈ {direct, agency, web, app}",
     "SELECT COUNT(*) FROM Booking WHERE booking_channel NOT IN ('direct','agency','web','app')"),
    ("PriceModifier.modifier_type ∈ {percentage, fixed}",
     "SELECT COUNT(*) FROM PriceModifier WHERE modifier_type NOT IN ('percentage','fixed')"),
    ("BookingModifier.modifier_type ∈ {percentage, fixed}",
     "SELECT COUNT(*) FROM BookingModifier WHERE modifier_type NOT IN ('percentage','fixed')"),
    ("RoomDay.status ∈ {available, occupied, maintenance}",
     "SELECT COUNT(*) FROM RoomDay WHERE status NOT IN ('available','occupied','maintenance')"),
    ("Invoice.status ∈ {paid, pending, refunded}",
     "SELECT COUNT(*) FROM Invoice WHERE status NOT IN ('paid','pending','refunded')"),
    ("Invoice.payment_method ∈ {card, transfer, cash}",
     "SELECT COUNT(*) FROM Invoice WHERE payment_method NOT IN ('card','transfer','cash')"),
]
for label, sql in domain_checks:
    n = q(sql)
    check(label, n == 0, n, 0)


# ── SECTION 6: BUSINESS RULES ────────────────────────────────────
print("\n[6] Business rules")

n = q("SELECT COUNT(*) FROM Booking WHERE check_out <= check_in")
check("check_out > check_in", n == 0, n)

n = q("SELECT COUNT(*) FROM Booking WHERE booking_date > check_in")
check("booking_date ≤ check_in", n == 0, n)

n = q("SELECT COUNT(*) FROM PriceModifier WHERE end_date < start_date")
check("PriceModifier end_date ≥ start_date", n == 0, n)

n = q("""
    SELECT COUNT(*) FROM Booking
    WHERE NOT (
        (booking_status = 'cancelled'
            AND cancellation_date IS NOT NULL AND cancellation_reason IS NOT NULL)
        OR
        (booking_status <> 'cancelled'
            AND cancellation_date IS NULL AND cancellation_reason IS NULL)
    )
""")
check("Cancellation fields consistent with booking_status", n == 0, n)

n = q("SELECT COUNT(*) FROM Customer WHERE commission_rate < 0 OR commission_rate > 1")
check("Customer.commission_rate ∈ [0, 1]", n == 0, n)

n = q("""
    SELECT COUNT(*) FROM Booking b
    JOIN Room r ON r.room_id = b.room_id
    WHERE b.guests > r.max_guests
""")
check("Booking guests ≤ room max_guests", n == 0, n)

n = q("""
    SELECT COUNT(*) FROM MeliaRewards mr
    JOIN Customer c ON c.customer_id = mr.customer_id
    WHERE c.customer_type <> 'direct'
""")
check("All MeliaRewards members are direct customers", n == 0, n)


# ── SECTION 7: TRIGGER CORRECTNESS ───────────────────────────────
# These verify that the AFTER INSERT trigger has done its job for every row.
print("\n[7] Trigger correctness")

# 7a. listed_price was filled in (i.e. SUM(price_per_day) > 0)
n = q("SELECT COUNT(*) FROM Booking WHERE listed_price = 0")
check("Every booking has a non-zero listed_price (placeholder cleared)",
      n == 0, n, 0)

# 7b. total_paid was filled in
n = q("SELECT COUNT(*) FROM Booking WHERE total_paid = 0")
check("Every booking has a non-zero total_paid (placeholder cleared)",
      n == 0, n, 0)

# 7c. listed_price actually equals SUM(RoomDay.price_per_day) for the stay
n = q("""
    SELECT COUNT(*) FROM Booking b
    WHERE ABS(b.listed_price - COALESCE((
        SELECT SUM(rd.price_per_day) FROM RoomDay rd
        WHERE rd.room_id = b.room_id
          AND rd.day >= b.check_in AND rd.day < b.check_out
    ), 0)) > 0.02
""")
check("listed_price = SUM(RoomDay.price_per_day) for every booking (±0.02)",
      n == 0, n, 0)

# 7d. Booking.total_paid matches Invoice.total_paid
n = q("""
    SELECT COUNT(*) FROM Booking b JOIN Invoice i ON i.booking_id = b.booking_id
    WHERE ABS(b.total_paid - i.total_paid) > 0.02
""")
check("Booking.total_paid = Invoice.total_paid (±0.02)", n == 0, n, 0)

# 7e. Invoice math is internally consistent
n = q("SELECT COUNT(*) FROM Invoice WHERE ABS(net_amount + tax_amount - total_paid) > 0.02")
check("Invoice: net + tax = total (±0.02)", n == 0, n, 0)

# 7f. tax_amount = ROUND(net × tax_rate)
n = q("""
    SELECT COUNT(*) FROM Invoice
    WHERE ABS(tax_amount - ROUND(net_amount * tax_rate, 2)) > 0.02
""")
check("Invoice tax_amount = net_amount × tax_rate (±0.02)", n == 0, n, 0)

# 7g. Every booking has exactly one Invoice (1:1)
n = q("SELECT COUNT(*) FROM Booking WHERE booking_id NOT IN (SELECT booking_id FROM Invoice)")
check("Every booking has an invoice (1:1)", n == 0, n, 0)

# 7h. RoomDay.status = 'occupied' iff some confirmed/completed booking covers that night
# OPTIMIZATION: pre-compute the set of (room_id, day) covered by active bookings
# into an INDEXED temp table. Without this, the correlated NOT EXISTS scans
# Booking once per occupied RoomDay row (≈O(M × N), runs in minutes).
# With the temp table + index it's an indexed lookup (≈O(M log N), seconds).
cur.execute("DROP TABLE IF EXISTS _covered_days")
cur.execute("""
    CREATE TEMP TABLE _covered_days AS
    SELECT DISTINCT b.room_id, rd.day
    FROM   Booking b
    JOIN   RoomDay rd
        ON rd.room_id = b.room_id
       AND rd.day >= b.check_in
       AND rd.day <  b.check_out
    WHERE  b.booking_status IN ('confirmed','completed')
""")
cur.execute("CREATE INDEX _covered_idx ON _covered_days(room_id, day)")

n = q("""
    SELECT COUNT(*) FROM RoomDay rd
    WHERE rd.status = 'occupied'
      AND NOT EXISTS (
          SELECT 1 FROM _covered_days c
          WHERE c.room_id = rd.room_id AND c.day = rd.day
      )
""")
check("Every 'occupied' day is covered by a confirmed/completed booking",
      n == 0, n, 0)
cur.execute("DROP TABLE _covered_days")

# 7i. Members earn points only on completed (non-cancelled) bookings
n = q("""
    SELECT COUNT(*) FROM Booking
    WHERE booking_status = 'cancelled' AND points_earned IS NOT NULL
""")
check("Cancelled bookings have NULL points_earned", n == 0, n, 0)

n = q("""
    SELECT COUNT(*) FROM Booking b
    WHERE b.customer_id NOT IN (SELECT customer_id FROM MeliaRewards)
      AND b.points_earned IS NOT NULL
""")
check("Non-member bookings have NULL points_earned", n == 0, n, 0)


# ── SECTION 8: COMPLETENESS ──────────────────────────────────────
print("\n[8] Completeness")

n = q("SELECT COUNT(*) FROM Hotel WHERE hotel_id NOT IN (SELECT DISTINCT hotel_id FROM Room)")
check("Every hotel has at least one room", n == 0, n, 0)

n = q("SELECT COUNT(*) FROM Room WHERE room_id NOT IN (SELECT DISTINCT room_id FROM RoomDay)")
check("Every room has at least one RoomDay entry", n == 0, n, 0)

min_days = q("SELECT MIN(cnt) FROM (SELECT room_id, COUNT(*) cnt FROM RoomDay GROUP BY room_id)")
check(f"Min RoomDay days per room: {min_days} (=1 095)",
      min_days == 1095, min_days, 1095)

n_brands_used = q("SELECT COUNT(DISTINCT brand_id) FROM Hotel")
check(f"All 5 brands assigned to a hotel: {n_brands_used}",
      n_brands_used == 5, n_brands_used, 5)

n_channels = q("SELECT COUNT(DISTINCT booking_channel) FROM Booking")
check(f"All 4 booking channels present: {n_channels}",
      n_channels == 4, n_channels, 4)

n_years = q("SELECT COUNT(DISTINCT strftime('%Y', check_in)) FROM Booking")
check(f"Bookings span 3 calendar years: {n_years}",
      n_years == 3, n_years, 3)


# ── SUMMARY ──────────────────────────────────────────────────────
print("\n" + "=" * 62)
total = passed + failed + warnings
print(f"  Results: {passed} passed  /  {warnings} warnings  /  {failed} failed")
print(f"  Total checks: {total}")
if failed == 0 and warnings == 0:
    print("  ✅  All checks passed.")
elif failed == 0:
    print(f"  ✅  No failures. Review {warnings} warning(s) above.")
else:
    print(f"  ❌  {failed} check(s) failed. Review the output above.")
print("=" * 62)

conn.close()
