"""
MELIA HOTELS — Schema Creator (v5)
====================================
Creates the complete database schema: tables, views, triggers, indexes.
Run this file BEFORE data.py.

Architecture principle:
  ALL business logic (math, pricing, side-effects, integrity) lives here.
  data.py performs only INSERTs of inputs; triggers compute the rest.

Order respected by data.py:
  Brands → Hotel → Room → RoomDay (calendar) → PriceModifier
                                              → Customer → MeliaRewards → Booking

Tables (10):
    Brands, Hotel, Room, PriceModifier, RoomDay,
    Customer, MeliaRewards, Booking, BookingModifier, Invoice

Views (2):
    v_calendar_days     — defines the official calendar range; used by data.py
                          to populate RoomDay via INSERT-SELECT.
    v_room_day_price    — single source of truth for the dynamic price formula;
                          consumed by the PriceModifier triggers and by
                          trg_room_base_rate_changed.

Triggers (9):
    trg_booking_check_availability   BEFORE INSERT  on Booking
    trg_pm_after_insert              AFTER  INSERT  on PriceModifier
    trg_pm_after_update              AFTER  UPDATE  on PriceModifier
    trg_pm_after_delete              AFTER  DELETE  on PriceModifier
    trg_booking_after_insert         AFTER  INSERT  on Booking
        (consolidated 9-step trigger: pricing, BookingModifier rows,
         Invoice, RoomDay status, MeliaRewards points)
    trg_block_completed_cancel       BEFORE UPDATE  on Booking
        (forbids cancelling a 'completed' booking — the stay has
         already taken place; cancellation is only valid for 'confirmed')
    trg_booking_after_cancel         AFTER  UPDATE  on Booking
        (on 'confirmed' → 'cancelled': frees RoomDay, refunds Invoice,
         NULL-outs Booking.points_earned)
    trg_booking_after_complete       AFTER  UPDATE  on Booking
        (credits MeliaRewards.points_balance when confirmed → completed)
    trg_room_base_rate_changed       AFTER  UPDATE  on Room
        (recomputes RoomDay prices for that room when base_rate changes)

Indexes (5, NEW in v5):
    idx_booking_customer  ON Booking(customer_id)
    idx_booking_dates     ON Booking(check_in, check_out)
    idx_booking_status    ON Booking(booking_status)
    idx_bm_modifier       ON BookingModifier(modifier_id)
    idx_invoice_status    ON Invoice(status)
"""

import sqlite3
from pathlib import Path

DB_PATH        = Path(__file__).parent / "melia.db"
CALENDAR_START = "2025-01-01"
CALENDAR_END   = "2027-12-31"
TAX_RATE       = 0.10                    # used in the trigger as literal 1.10 / 0.10

conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA journal_mode = WAL")
cur = conn.cursor()

# ===========================================================
# BLOCK 1: DROP everything (reverse dependency order)
# ===========================================================
print("Dropping existing objects...")

drops = [
    # Triggers (new in v4)
    "DROP TRIGGER IF EXISTS trg_block_completed_cancel",
    "DROP TRIGGER IF EXISTS trg_room_base_rate_changed",
    "DROP TRIGGER IF EXISTS trg_booking_after_complete",
    "DROP TRIGGER IF EXISTS trg_booking_after_cancel",
    # Triggers (existing)
    "DROP TRIGGER IF EXISTS trg_booking_after_insert",
    "DROP TRIGGER IF EXISTS trg_pm_after_delete",
    "DROP TRIGGER IF EXISTS trg_pm_after_update",
    "DROP TRIGGER IF EXISTS trg_pm_after_insert",
    "DROP TRIGGER IF EXISTS trg_booking_check_availability",
    # Views
    "DROP VIEW    IF EXISTS v_room_day_price",
    "DROP VIEW    IF EXISTS v_calendar_days",
    # Tables (their indexes are dropped automatically with the table)
    "DROP TABLE   IF EXISTS Invoice",
    "DROP TABLE   IF EXISTS BookingModifier",
    "DROP TABLE   IF EXISTS Booking",
    "DROP TABLE   IF EXISTS MeliaRewards",
    "DROP TABLE   IF EXISTS Customer",
    "DROP TABLE   IF EXISTS RoomDay",
    "DROP TABLE   IF EXISTS PriceModifier",
    "DROP TABLE   IF EXISTS Room",
    "DROP TABLE   IF EXISTS Hotel",
    "DROP TABLE   IF EXISTS Brands",
]
for stmt in drops:
    conn.execute(stmt)
conn.commit()
print("  ✓ Done.")


# ===========================================================
# BLOCK 2: CREATE TABLES
# ===========================================================
# Note: listed_price, total_paid, points_earned in Booking are populated by
# the AFTER INSERT trigger. Their CHECKs are written so that the placeholder
# values inserted by data.py (0 for prices, NULL for points_earned) pass
# validation; the trigger then overwrites them with the real values.
# ===========================================================
print("Creating tables...")

conn.execute("""
CREATE TABLE Brands (
    brand_id        INTEGER PRIMARY KEY,
    name            VARCHAR NOT NULL,
    segment         VARCHAR,
    description     TEXT,
    target_audience VARCHAR
)
""")

conn.execute("""
CREATE TABLE Hotel (
    hotel_id  INTEGER PRIMARY KEY,
    name      VARCHAR NOT NULL,
    country   VARCHAR,
    city      VARCHAR,
    brand_id  INTEGER NOT NULL,
    FOREIGN KEY (brand_id) REFERENCES Brands(brand_id)
)
""")

conn.execute("""
CREATE TABLE Room (
    room_id     INTEGER PRIMARY KEY,
    room_number VARCHAR NOT NULL,
    room_type   VARCHAR NOT NULL,
    base_rate   DECIMAL NOT NULL CHECK (base_rate > 0),
    max_guests  INTEGER NOT NULL CHECK (max_guests >= 1),
    hotel_id    INTEGER NOT NULL,
    FOREIGN KEY (hotel_id) REFERENCES Hotel(hotel_id)
)
""")

conn.execute("""
CREATE TABLE PriceModifier (
    modifier_id         INTEGER PRIMARY KEY,
    name                VARCHAR NOT NULL,
    modifier_type       VARCHAR NOT NULL CHECK (modifier_type IN ('percentage','fixed')),
    value               DECIMAL NOT NULL,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    hotel_id            INTEGER,
    min_points_required INTEGER CHECK (min_points_required IS NULL OR min_points_required >= 0),
    weekends_only       BOOLEAN CHECK (weekends_only IS NULL OR weekends_only IN (0,1)),
    FOREIGN KEY (hotel_id) REFERENCES Hotel(hotel_id),
    CHECK (end_date >= start_date)
)
""")

conn.execute("""
CREATE TABLE RoomDay (
    room_id       INTEGER NOT NULL,
    day           DATE    NOT NULL,
    price_per_day DECIMAL NOT NULL CHECK (price_per_day >= 0),
    status        VARCHAR NOT NULL DEFAULT 'available'
                  CHECK (status IN ('available','occupied','maintenance')),
    PRIMARY KEY (room_id, day),
    FOREIGN KEY (room_id) REFERENCES Room(room_id)
)
""")

conn.execute("""
CREATE TABLE Customer (
    customer_id     INTEGER PRIMARY KEY,
    name            VARCHAR NOT NULL,
    customer_type   VARCHAR NOT NULL CHECK (customer_type IN ('direct','agency')),
    email           VARCHAR UNIQUE,
    nationality     VARCHAR,
    commission_rate DECIMAL NOT NULL DEFAULT 0.00
                    CHECK (commission_rate BETWEEN 0 AND 1)
)
""")

conn.execute("""
CREATE TABLE MeliaRewards (
    member_id      INTEGER PRIMARY KEY,
    customer_id    INTEGER NOT NULL UNIQUE,
    points_balance INTEGER NOT NULL DEFAULT 0 CHECK (points_balance >= 0),
    join_date      DATE    NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES Customer(customer_id)
)
""")

conn.execute("""
CREATE TABLE Booking (
    booking_id          INTEGER PRIMARY KEY,
    customer_id         INTEGER NOT NULL,
    room_id             INTEGER NOT NULL,
    booking_date        DATE    NOT NULL,
    check_in            DATE    NOT NULL,
    check_out           DATE    NOT NULL,
    booking_status      VARCHAR NOT NULL
                        CHECK (booking_status IN ('confirmed','cancelled','completed')),
    booking_channel     VARCHAR NOT NULL
                        CHECK (booking_channel IN ('direct','agency','web','app')),
    guests              INTEGER NOT NULL CHECK (guests >= 1),
    listed_price        DECIMAL NOT NULL DEFAULT 0 CHECK (listed_price >= 0),
                                   -- placeholder; filled in by trg_booking_after_insert
    total_paid          DECIMAL NOT NULL DEFAULT 0 CHECK (total_paid >= 0),
                                   -- placeholder; filled in by trg_booking_after_insert
    points_earned       INTEGER CHECK (points_earned IS NULL OR points_earned >= 0),
                                   -- left NULL on insert; filled in by trg_booking_after_insert
    cancellation_date   DATE,
    cancellation_reason VARCHAR,
    FOREIGN KEY (customer_id) REFERENCES Customer(customer_id),
    FOREIGN KEY (room_id)     REFERENCES Room(room_id),
    CHECK (check_out > check_in),
    CHECK (booking_date <= check_in),
    CHECK (
        (booking_status = 'cancelled'
         AND cancellation_date IS NOT NULL
         AND cancellation_reason IS NOT NULL)
        OR
        (booking_status <> 'cancelled'
         AND cancellation_date IS NULL
         AND cancellation_reason IS NULL)
    )
)
""")

conn.execute("""
CREATE TABLE BookingModifier (
    booking_id     INTEGER NOT NULL,
    modifier_id    INTEGER NOT NULL,
    modifier_type  VARCHAR NOT NULL CHECK (modifier_type IN ('percentage','fixed')),
    discount_value DECIMAL NOT NULL,
    PRIMARY KEY (booking_id, modifier_id),
    FOREIGN KEY (booking_id)  REFERENCES Booking(booking_id),
    FOREIGN KEY (modifier_id) REFERENCES PriceModifier(modifier_id)
)
""")

conn.execute("""
CREATE TABLE Invoice (
    invoice_id     INTEGER PRIMARY KEY,
    booking_id     INTEGER NOT NULL UNIQUE,
    issue_date     DATE    NOT NULL,
    net_amount     DECIMAL NOT NULL CHECK (net_amount >= 0),
    tax_rate       DECIMAL NOT NULL CHECK (tax_rate BETWEEN 0 AND 1),
    tax_amount     DECIMAL NOT NULL CHECK (tax_amount >= 0),
    total_paid     DECIMAL NOT NULL CHECK (total_paid >= 0),
    status         VARCHAR NOT NULL CHECK (status IN ('paid','pending','refunded')),
    payment_method VARCHAR NOT NULL CHECK (payment_method IN ('card','transfer','cash')),
    payment_date   DATE,
    FOREIGN KEY (booking_id) REFERENCES Booking(booking_id)
)
""")

conn.commit()
print("  ✓ 10 tables created.")


# ===========================================================
# BLOCK 3: VIEWS
# ===========================================================
print("Creating views...")

# ── v_calendar_days — defines the official calendar window ───────────────
# Returns one row per date in the configured range. Used by data.py to
# populate RoomDay via INSERT-SELECT (CROSS JOIN with Room).
# Changing the range here is the single point of edit for the calendar.
conn.execute(f"""
CREATE VIEW v_calendar_days AS
WITH RECURSIVE dates(day) AS (
    SELECT DATE('{CALENDAR_START}')
    UNION ALL
    SELECT DATE(day, '+1 day') FROM dates WHERE day < DATE('{CALENDAR_END}')
)
SELECT day FROM dates
""")

# ── v_room_day_price — single source of truth for nightly price ──────────
# Computes what RoomDay.price_per_day SHOULD be for each (room, day)
# given the current PriceModifier catalogue. Consumed by the three
# trg_pm_after_* triggers to keep RoomDay in sync.
#
# Application order:
#   1) percentage modifiers — multiplied together (recursive CTE)
#   2) fixed modifiers      — summed and added
#
# Member-only modifiers (min_points_required IS NOT NULL) are excluded
# here; those are applied at booking time, not in the calendar.
conn.execute("""
CREATE VIEW v_room_day_price AS
WITH RECURSIVE
applicable AS (
    SELECT r.room_id,
           rd.day,
           pm.modifier_id,
           pm.modifier_type,
           pm.value
    FROM   Room r
    JOIN   RoomDay rd ON rd.room_id = r.room_id
    JOIN   PriceModifier pm
        ON pm.min_points_required IS NULL
       AND rd.day BETWEEN pm.start_date AND pm.end_date
       AND (pm.hotel_id IS NULL OR pm.hotel_id = r.hotel_id)
       AND (COALESCE(pm.weekends_only, 0) = 0
            OR strftime('%w', rd.day) IN ('0','6'))
),
pct_numbered AS (
    SELECT room_id, day, value,
           ROW_NUMBER() OVER (PARTITION BY room_id, day ORDER BY modifier_id) AS rn,
           COUNT(*)     OVER (PARTITION BY room_id, day)                      AS total
    FROM   applicable
    WHERE  modifier_type = 'percentage'
),
pct_chain AS (
    SELECT room_id, day, rn, total, (1.0 + value) AS factor
    FROM   pct_numbered WHERE rn = 1
    UNION ALL
    SELECT pn.room_id, pn.day, pn.rn, pn.total, pc.factor * (1.0 + pn.value)
    FROM   pct_numbered pn
    JOIN   pct_chain    pc
        ON pc.room_id = pn.room_id
       AND pc.day     = pn.day
       AND pn.rn      = pc.rn + 1
),
pct_factor AS (
    SELECT room_id, day, factor
    FROM   pct_chain WHERE rn = total
),
fixed_sum AS (
    SELECT room_id, day, SUM(value) AS total_fixed
    FROM   applicable
    WHERE  modifier_type = 'fixed'
    GROUP  BY room_id, day
)
SELECT
    r.room_id,
    rd.day,
    ROUND(
        r.base_rate * COALESCE(pf.factor, 1.0)
        + COALESCE(fs.total_fixed, 0)
    , 2) AS computed_price
FROM   Room r
JOIN   RoomDay rd ON rd.room_id = r.room_id
LEFT   JOIN pct_factor pf ON pf.room_id = r.room_id AND pf.day = rd.day
LEFT   JOIN fixed_sum  fs ON fs.room_id = r.room_id AND fs.day = rd.day
""")

conn.commit()
print("  ✓ 2 views created.")


# ===========================================================
# BLOCK 4: TRIGGERS
# ===========================================================
print("Creating triggers...")

# ── trg_booking_check_availability ───────────────────────────────────────
# BEFORE INSERT: rejects an ACTIVE booking ('confirmed' or 'completed')
# if the room is not 'available' for every requested night, or if any
# night falls outside the RoomDay calendar (the calendar boundary is a
# hard constraint).
#
# Cancelled bookings are intentionally exempt from this check. A
# 'cancelled' row is a historical record of an aborted reservation
# attempt — it does not claim the room and therefore does not require
# the calendar to grant the nights. This matters mainly for seeding,
# where a chronologically later cancellation may overlap a
# chronologically earlier confirmed booking; without this WHEN clause
# the cancellation would be wrongly rejected.
#
# For the future form-app (which only INSERTs 'confirmed' bookings),
# behaviour is unchanged: an active booking on already-occupied days
# is still rejected.
conn.execute("""
CREATE TRIGGER trg_booking_check_availability
BEFORE INSERT ON Booking
WHEN NEW.booking_status IN ('confirmed', 'completed')
BEGIN
    SELECT CASE
        WHEN (
            SELECT COUNT(*)
            FROM   RoomDay
            WHERE  room_id = NEW.room_id
              AND  day     >= NEW.check_in
              AND  day     <  NEW.check_out
              AND  status  = 'available'
        ) < CAST(julianday(NEW.check_out) - julianday(NEW.check_in) AS INTEGER)
        THEN RAISE(ABORT, 'Booking rejected: one or more requested nights are not available or fall outside the configured RoomDay calendar.')
    END;
END
""")

# ── trg_pm_after_insert ──────────────────────────────────────────────────
# Whenever a PUBLIC PriceModifier is added, recompute the affected
# RoomDay.price_per_day rows from v_room_day_price.
conn.execute("""
CREATE TRIGGER trg_pm_after_insert
AFTER INSERT ON PriceModifier
WHEN NEW.min_points_required IS NULL
BEGIN
    UPDATE RoomDay
    SET    price_per_day = (
        SELECT computed_price
        FROM   v_room_day_price
        WHERE  room_id = RoomDay.room_id
          AND  day     = RoomDay.day
    )
    WHERE  day BETWEEN NEW.start_date AND NEW.end_date
      AND  room_id IN (
               SELECT room_id FROM Room
               WHERE  NEW.hotel_id IS NULL OR hotel_id = NEW.hotel_id
           )
      AND  (COALESCE(NEW.weekends_only, 0) = 0
            OR strftime('%w', day) IN ('0','6'));
END
""")

# ── trg_pm_after_update ──────────────────────────────────────────────────
# Reprices RoomDay rows affected by both the OLD and the NEW modifier shape.
conn.execute("""
CREATE TRIGGER trg_pm_after_update
AFTER UPDATE ON PriceModifier
WHEN NEW.min_points_required IS NULL OR OLD.min_points_required IS NULL
BEGIN
    UPDATE RoomDay
    SET    price_per_day = (
        SELECT computed_price
        FROM   v_room_day_price
        WHERE  room_id = RoomDay.room_id
          AND  day     = RoomDay.day
    )
    WHERE  day BETWEEN OLD.start_date AND OLD.end_date
      AND  room_id IN (
               SELECT room_id FROM Room
               WHERE  OLD.hotel_id IS NULL OR hotel_id = OLD.hotel_id
           )
      AND  (COALESCE(OLD.weekends_only, 0) = 0
            OR strftime('%w', day) IN ('0','6'));

    UPDATE RoomDay
    SET    price_per_day = (
        SELECT computed_price
        FROM   v_room_day_price
        WHERE  room_id = RoomDay.room_id
          AND  day     = RoomDay.day
    )
    WHERE  day BETWEEN NEW.start_date AND NEW.end_date
      AND  room_id IN (
               SELECT room_id FROM Room
               WHERE  NEW.hotel_id IS NULL OR hotel_id = NEW.hotel_id
           )
      AND  (COALESCE(NEW.weekends_only, 0) = 0
            OR strftime('%w', day) IN ('0','6'));
END
""")

# ── trg_pm_after_delete ──────────────────────────────────────────────────
# Reprices RoomDay rows affected by the deleted PUBLIC modifier.
conn.execute("""
CREATE TRIGGER trg_pm_after_delete
AFTER DELETE ON PriceModifier
WHEN OLD.min_points_required IS NULL
BEGIN
    UPDATE RoomDay
    SET    price_per_day = (
        SELECT computed_price
        FROM   v_room_day_price
        WHERE  room_id = RoomDay.room_id
          AND  day     = RoomDay.day
    )
    WHERE  day BETWEEN OLD.start_date AND OLD.end_date
      AND  room_id IN (
               SELECT room_id FROM Room
               WHERE  OLD.hotel_id IS NULL OR hotel_id = OLD.hotel_id
           )
      AND  (COALESCE(OLD.weekends_only, 0) = 0
            OR strftime('%w', day) IN ('0','6'));
END
""")

# ── trg_booking_after_insert ─────────────────────────────────────────────
# The big one. AFTER a booking row lands in the DB (with placeholder
# 0/0/NULL for listed_price/total_paid/points_earned), this trigger:
#   1. Computes listed_price = SUM of price_per_day across the stay.
#   2. Computes total_paid by chaining (in this order):
#         × (1 + best applicable member percentage modifier)
#         + sum of applicable member fixed modifiers
#         × (1 − customer.commission_rate)
#         × (1 + tax_rate)
#   3. Sets points_earned = FLOOR(net_amount) for non-cancelled member
#      bookings; NULL otherwise.
#   4. Inserts one BookingModifier row per public modifier that touched
#      any night of the stay.
#   5. Inserts the best member percentage modifier (if any) as a row.
#   6. Inserts every applicable member fixed modifier (e.g. Sol welcome
#      bonus) as rows.
#   7. Creates the matching Invoice (status derived from booking_status).
#   8. Flips RoomDay.status to 'occupied' for confirmed/completed.
#   9. Increments MeliaRewards.points_balance for completed member bookings.
#
# Member tier resolution:
#   The "best" tier is the member-only percentage modifier with the highest
#   min_points_required threshold that the customer's current
#   points_balance still satisfies. Done with ORDER BY min_points_required
#   DESC LIMIT 1.
#
# Date filtering:
#   Every modifier lookup uses NEW.check_in BETWEEN start_date AND end_date.
#   This means a modifier whose validity does not cover the booking's
#   check-in date does NOT apply, even if it covers later nights of the
#   stay. This matches the intent of "the modifier rules of the day you
#   commit to the stay".
conn.execute("""
CREATE TRIGGER trg_booking_after_insert
AFTER INSERT ON Booking
BEGIN
    -- Step 1: listed_price = SUM(price_per_day for each booked night)
    UPDATE Booking
    SET    listed_price = COALESCE((
        SELECT SUM(price_per_day)
        FROM   RoomDay
        WHERE  room_id = NEW.room_id
          AND  day     >= NEW.check_in
          AND  day     <  NEW.check_out
    ), 0)
    WHERE  booking_id = NEW.booking_id;

    -- Step 2: total_paid = (((listed × (1+pct_member)) + sum_fixed_member)
    --                       × (1 − commission)) × (1 + tax_rate)
    UPDATE Booking
    SET    total_paid = ROUND(
        (
            listed_price
            * (1 + COALESCE((
                SELECT pm.value
                FROM   PriceModifier pm, MeliaRewards mr
                WHERE  mr.customer_id = NEW.customer_id
                  AND  pm.modifier_type = 'percentage'
                  AND  pm.min_points_required IS NOT NULL
                  AND  mr.points_balance >= pm.min_points_required
                  AND  NEW.check_in BETWEEN pm.start_date AND pm.end_date
                ORDER BY pm.min_points_required DESC
                LIMIT 1
              ), 0))
            + COALESCE((
                SELECT SUM(pm.value)
                FROM   PriceModifier pm
                JOIN   MeliaRewards mr ON mr.customer_id = NEW.customer_id
                JOIN   Room r           ON r.room_id     = NEW.room_id
                WHERE  pm.modifier_type = 'fixed'
                  AND  pm.min_points_required IS NOT NULL
                  AND  mr.points_balance >= pm.min_points_required
                  AND  NEW.check_in BETWEEN pm.start_date AND pm.end_date
                  AND  (pm.hotel_id IS NULL OR pm.hotel_id = r.hotel_id)
              ), 0)
        )
        * (1 - (SELECT commission_rate FROM Customer WHERE customer_id = NEW.customer_id))
        * 1.10
    , 2)
    WHERE  booking_id = NEW.booking_id;

    -- Step 3: points_earned = FLOOR(net_amount) for active member bookings
    UPDATE Booking
    SET    points_earned = CASE
        WHEN EXISTS (SELECT 1 FROM MeliaRewards WHERE customer_id = NEW.customer_id)
         AND NEW.booking_status <> 'cancelled'
        THEN CAST(total_paid / 1.10 AS INTEGER)   -- truncates positive → FLOOR
        ELSE NULL
    END
    WHERE  booking_id = NEW.booking_id;

    -- Step 4: Public BookingModifier rows (one per modifier that applied
    --         on at least one night of the stay)
    INSERT INTO BookingModifier (booking_id, modifier_id, modifier_type, discount_value)
    SELECT DISTINCT NEW.booking_id, pm.modifier_id, pm.modifier_type, pm.value
    FROM   PriceModifier pm
    JOIN   RoomDay rd ON rd.room_id = NEW.room_id
                     AND rd.day    >= NEW.check_in
                     AND rd.day    <  NEW.check_out
    JOIN   Room    r  ON r.room_id  = NEW.room_id
    WHERE  pm.min_points_required IS NULL
      AND  rd.day BETWEEN pm.start_date AND pm.end_date
      AND  (pm.hotel_id IS NULL OR pm.hotel_id = r.hotel_id)
      AND  (COALESCE(pm.weekends_only, 0) = 0
            OR strftime('%w', rd.day) IN ('0','6'));

    -- Step 5: Member percentage modifier (best qualifying tier)
    INSERT INTO BookingModifier (booking_id, modifier_id, modifier_type, discount_value)
    SELECT NEW.booking_id, pm.modifier_id, pm.modifier_type, pm.value
    FROM   PriceModifier pm, MeliaRewards mr
    WHERE  mr.customer_id = NEW.customer_id
      AND  pm.modifier_type = 'percentage'
      AND  pm.min_points_required IS NOT NULL
      AND  mr.points_balance >= pm.min_points_required
      AND  NEW.check_in BETWEEN pm.start_date AND pm.end_date
    ORDER  BY pm.min_points_required DESC
    LIMIT  1;

    -- Step 6: Member fixed modifiers (e.g. Sol welcome bonus)
    INSERT INTO BookingModifier (booking_id, modifier_id, modifier_type, discount_value)
    SELECT NEW.booking_id, pm.modifier_id, pm.modifier_type, pm.value
    FROM   PriceModifier pm
    JOIN   MeliaRewards  mr ON mr.customer_id = NEW.customer_id
    JOIN   Room          r  ON r.room_id      = NEW.room_id
    WHERE  pm.modifier_type = 'fixed'
      AND  pm.min_points_required IS NOT NULL
      AND  mr.points_balance >= pm.min_points_required
      AND  NEW.check_in BETWEEN pm.start_date AND pm.end_date
      AND  (pm.hotel_id IS NULL OR pm.hotel_id = r.hotel_id);

    -- Step 7: Invoice (issue_date = booking_date; payment_method default 'card')
    INSERT INTO Invoice (
        invoice_id, booking_id, issue_date,
        net_amount, tax_rate, tax_amount, total_paid,
        status, payment_method, payment_date
    )
    SELECT
        NEW.booking_id,                                        -- invoice_id mirrors booking_id (1:1)
        NEW.booking_id,                                        -- booking_id FK
        NEW.booking_date,                                      -- issue_date
        ROUND(b.total_paid / 1.10, 2),                         -- net_amount
        0.10,                                                   -- tax_rate
        ROUND(b.total_paid - ROUND(b.total_paid / 1.10, 2), 2),-- tax_amount
        b.total_paid,                                           -- total_paid
        CASE WHEN NEW.booking_status = 'cancelled' THEN 'refunded' ELSE 'paid' END,
        'card',
        CASE WHEN NEW.booking_status = 'cancelled' THEN NEW.cancellation_date
             ELSE NEW.booking_date END
    FROM Booking b
    WHERE b.booking_id = NEW.booking_id;

    -- Step 8: RoomDay → 'occupied' for confirmed/completed bookings
    UPDATE RoomDay
    SET    status = 'occupied'
    WHERE  NEW.booking_status IN ('confirmed','completed')
      AND  room_id = NEW.room_id
      AND  day >= NEW.check_in
      AND  day <  NEW.check_out;

    -- Step 9: Credit MeliaRewards.points_balance for completed bookings
    UPDATE MeliaRewards
    SET    points_balance = points_balance + (
        SELECT points_earned FROM Booking
        WHERE  booking_id = NEW.booking_id AND points_earned IS NOT NULL
    )
    WHERE  customer_id = NEW.customer_id
      AND  NEW.booking_status = 'completed'
      AND  EXISTS (
          SELECT 1 FROM Booking
          WHERE  booking_id = NEW.booking_id AND points_earned IS NOT NULL
      );
END
""")

# ── trg_block_completed_cancel  (BEFORE UPDATE) ──────────────────────────
# Forbids transitioning a 'completed' booking to 'cancelled'. Once the
# guest has stayed, the booking is a historical fact — refunds and
# disputes happen via different processes, never by retroactively
# "cancelling" the stay.
#
# Defence in depth: the form-app already filters this out, but the
# schema-level guard means any direct SQL or future client code is
# protected too.
conn.execute("""
CREATE TRIGGER trg_block_completed_cancel
BEFORE UPDATE ON Booking
WHEN OLD.booking_status = 'completed' AND NEW.booking_status = 'cancelled'
BEGIN
    SELECT RAISE(ABORT,
        'Cannot cancel a completed booking. The stay has already taken place.');
END
""")

# ── trg_booking_after_cancel ─────────────────────────────────────────────
# Fires only on 'confirmed' → 'cancelled'. ('completed' → 'cancelled' is
# blocked upstream by trg_block_completed_cancel, so we never reach here
# with OLD.booking_status = 'completed'.)
#
# Steps:
#   1. NULL-out Booking.points_earned for consistency with the convention
#      "cancelled bookings carry NULL points_earned". The points were
#      pre-computed at INSERT time but never credited (because confirmed,
#      not completed), so no clawback is needed.
#   2. Free the corresponding RoomDay nights (only those currently
#      'occupied' — never touch 'maintenance' rows).
#   3. Mark the matching Invoice as 'refunded' and set its payment_date to
#      NEW.cancellation_date.
conn.execute("""
CREATE TRIGGER trg_booking_after_cancel
AFTER UPDATE ON Booking
WHEN OLD.booking_status = 'confirmed' AND NEW.booking_status = 'cancelled'
BEGIN
    UPDATE Booking
    SET    points_earned = NULL
    WHERE  booking_id = NEW.booking_id;

    UPDATE RoomDay
    SET    status = 'available'
    WHERE  room_id = NEW.room_id
      AND  day >= NEW.check_in
      AND  day <  NEW.check_out
      AND  status = 'occupied';

    UPDATE Invoice
    SET    status        = 'refunded',
           payment_date  = NEW.cancellation_date
    WHERE  booking_id = NEW.booking_id;
END
""")

# ── trg_booking_after_complete  (NEW v5) ─────────────────────────────────
# Fires when a 'confirmed' booking transitions to 'completed' (the guest
# actually stayed). Credits MeliaRewards.points_balance with the points
# that trg_booking_after_insert had pre-computed but had not yet awarded
# (because the original insert was as 'confirmed', not 'completed').
#
# Members only — non-member bookings have points_earned IS NULL.
conn.execute("""
CREATE TRIGGER trg_booking_after_complete
AFTER UPDATE ON Booking
WHEN OLD.booking_status = 'confirmed' AND NEW.booking_status = 'completed'
BEGIN
    UPDATE MeliaRewards
    SET    points_balance = points_balance + (
        SELECT points_earned FROM Booking
        WHERE  booking_id = NEW.booking_id AND points_earned IS NOT NULL
    )
    WHERE  customer_id = NEW.customer_id
      AND  EXISTS (
          SELECT 1 FROM Booking
          WHERE  booking_id = NEW.booking_id AND points_earned IS NOT NULL
      );
END
""")

# ── trg_room_base_rate_changed  (NEW v5) ─────────────────────────────────
# When a room's base_rate is updated, recompute every RoomDay row of that
# room from v_room_day_price. Without this, RoomDay would be stale until
# the next PriceModifier change touched the room.
#
# WHEN clause filters out no-op updates (SET base_rate = same_value).
conn.execute("""
CREATE TRIGGER trg_room_base_rate_changed
AFTER UPDATE OF base_rate ON Room
WHEN OLD.base_rate <> NEW.base_rate
BEGIN
    UPDATE RoomDay
    SET    price_per_day = (
        SELECT computed_price
        FROM   v_room_day_price
        WHERE  room_id = RoomDay.room_id
          AND  day     = RoomDay.day
    )
    WHERE  room_id = NEW.room_id;
END
""")

conn.commit()
print("  ✓ 9 triggers created.")


# ===========================================================
# BLOCK 5: INDEXES  (NEW in v5)
# ===========================================================
# Secondary indexes on the columns most often filtered/joined on.
# Primary keys and UNIQUE constraints already create implicit indexes;
# these add coverage for query patterns the dashboard, analytics and
# trigger logic actually use.
print("Creating indexes...")

conn.execute("CREATE INDEX idx_booking_customer ON Booking(customer_id)")
conn.execute("CREATE INDEX idx_booking_dates    ON Booking(check_in, check_out)")
conn.execute("CREATE INDEX idx_booking_status   ON Booking(booking_status)")
conn.execute("CREATE INDEX idx_bm_modifier      ON BookingModifier(modifier_id)")
conn.execute("CREATE INDEX idx_invoice_status   ON Invoice(status)")

conn.commit()
print("  ✓ 5 indexes created.")

print(f"\n✅ Schema creation complete (calendar: {CALENDAR_START} → {CALENDAR_END}).")
print("   Run data.py next.")
conn.close()
