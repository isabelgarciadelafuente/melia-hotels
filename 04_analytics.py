"""
MELIA HOTELS — KPI Analytics
Computes all 11 KPIs defined in the Analytics Objective section.
Run after schema.py + data.py.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "melia.db"
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def run(title, sql):
    print(f"\n{'=' * 62}")
    print(f"  {title}")
    print('=' * 62)
    cur.execute(sql)
    rows = cur.fetchall()
    if not rows:
        print("  (no results)")
        return rows
    keys = rows[0].keys()
    col_w = [max(len(k), max(len(str(r[k])) for r in rows)) for k in keys]
    header = "  " + "  ".join(k.ljust(w) for k, w in zip(keys, col_w))
    print(header)
    print("  " + "  ".join("-" * w for w in col_w))
    for row in rows:
        print("  " + "  ".join(str(row[k]).ljust(w) for k, w in zip(keys, col_w)))
    return rows


# ── KPI 1 — BOOKING LEAD TIME ────────────────────────────────────────────────
run(
    "KPI 1 · Booking Lead Time  (avg days booking→check-in, by segment)",
    """
    SELECT
        c.customer_type                                              AS segment,
        COUNT(*)                                                     AS bookings,
        ROUND(AVG(julianday(b.check_in) - julianday(b.booking_date)), 1)
                                                                     AS avg_lead_time_days,
        ROUND(MIN(julianday(b.check_in) - julianday(b.booking_date)), 0)
                                                                     AS min_days,
        ROUND(MAX(julianday(b.check_in) - julianday(b.booking_date)), 0)
                                                                     AS max_days
    FROM Booking b
    JOIN Customer c ON c.customer_id = b.customer_id
    WHERE b.booking_status IN ('confirmed', 'completed')
    GROUP BY c.customer_type
    ORDER BY avg_lead_time_days DESC
    """
)

run(
    "KPI 1b · Booking Lead Time  (by booking channel)",
    """
    SELECT
        b.booking_channel                                            AS channel,
        COUNT(*)                                                     AS bookings,
        ROUND(AVG(julianday(b.check_in) - julianday(b.booking_date)), 1)
                                                                     AS avg_lead_time_days
    FROM Booking b
    WHERE b.booking_status IN ('confirmed', 'completed')
    GROUP BY b.booking_channel
    ORDER BY avg_lead_time_days DESC
    """
)


# ── KPI 2 — AVERAGE LENGTH OF STAY ───────────────────────────────────────────
run(
    "KPI 2 · Average Length of Stay  (by customer type and room type)",
    """
    SELECT
        c.customer_type                                              AS customer_type,
        r.room_type                                                  AS room_type,
        COUNT(*)                                                     AS bookings,
        ROUND(AVG(julianday(b.check_out) - julianday(b.check_in)), 1)
                                                                     AS avg_nights
    FROM Booking b
    JOIN Customer c ON c.customer_id = b.customer_id
    JOIN Room     r ON r.room_id     = b.room_id
    WHERE b.booking_status IN ('confirmed', 'completed')
    GROUP BY c.customer_type, r.room_type
    ORDER BY c.customer_type, avg_nights DESC
    """
)

run(
    "KPI 2b · Average Length of Stay  (by brand segment)",
    """
    SELECT
        br.segment                                                   AS brand_segment,
        COUNT(*)                                                     AS bookings,
        ROUND(AVG(julianday(b.check_out) - julianday(b.check_in)), 1)
                                                                     AS avg_nights
    FROM Booking b
    JOIN Room   r  ON r.room_id   = b.room_id
    JOIN Hotel  h  ON h.hotel_id  = r.hotel_id
    JOIN Brands br ON br.brand_id = h.brand_id
    WHERE b.booking_status IN ('confirmed', 'completed')
    GROUP BY br.segment
    ORDER BY avg_nights DESC
    """
)


# ── KPI 3 — RETURN RATE ───────────────────────────────────────────────────────
run(
    "KPI 3 · Return Rate  (customers with >1 completed booking, by type)",
    """
    WITH stays AS (
        SELECT b.customer_id,
               c.customer_type,
               COUNT(*) AS completed_bookings
        FROM Booking b
        JOIN Customer c ON c.customer_id = b.customer_id
        WHERE b.booking_status = 'completed'
        GROUP BY b.customer_id, c.customer_type
    )
    SELECT
        customer_type,
        COUNT(*)                                                          AS customers_who_stayed,
        SUM(CASE WHEN completed_bookings > 1 THEN 1 ELSE 0 END)          AS returning_customers,
        ROUND(100.0 *
              SUM(CASE WHEN completed_bookings > 1 THEN 1 ELSE 0 END)
              / COUNT(*), 1)                                              AS return_rate_pct
    FROM stays
    GROUP BY customer_type
    ORDER BY return_rate_pct DESC
    """
)

run(
    "KPI 3b · Return Rate  (overall)",
    """
    WITH stays AS (
        SELECT customer_id, COUNT(*) AS completed_bookings
        FROM Booking
        WHERE booking_status = 'completed'
        GROUP BY customer_id
    )
    SELECT
        COUNT(*)                                                          AS total_customers,
        SUM(CASE WHEN completed_bookings > 1 THEN 1 ELSE 0 END)          AS returning_customers,
        ROUND(100.0 *
              SUM(CASE WHEN completed_bookings > 1 THEN 1 ELSE 0 END)
              / COUNT(*), 1)                                              AS return_rate_pct
    FROM stays
    """
)


# ── KPI 4 — POINTS ACCUMULATION RATE ────────────────────────────────────────
run(
    "KPI 4 · Points Accumulation Rate  (avg points earned per stay, by loyalty tier)",
    """
    WITH member_stays AS (
        SELECT
            b.customer_id,
            b.points_earned,
            CASE
                WHEN mr.points_balance >= 30000 THEN 'Platinum'
                WHEN mr.points_balance >= 15000 THEN 'Gold'
                WHEN mr.points_balance >= 5000  THEN 'Silver'
                ELSE                                 'Basic'
            END AS loyalty_tier
        FROM Booking b
        JOIN MeliaRewards mr ON mr.customer_id = b.customer_id
        WHERE b.booking_status  = 'completed'
          AND b.points_earned  IS NOT NULL
    )
    SELECT
        loyalty_tier,
        COUNT(*)                              AS completed_stays,
        ROUND(AVG(points_earned), 0)          AS avg_points_per_stay,
        ROUND(MIN(points_earned), 0)          AS min_points,
        ROUND(MAX(points_earned), 0)          AS max_points,
        SUM(points_earned)                    AS total_points_earned
    FROM member_stays
    GROUP BY loyalty_tier
    ORDER BY CASE loyalty_tier
        WHEN 'Platinum' THEN 1
        WHEN 'Gold'     THEN 2
        WHEN 'Silver'   THEN 3
        ELSE 4 END
    """
)


# ── KPI 5 — REVENUE PER CUSTOMER SEGMENT ────────────────────────────────────
run(
    "KPI 5 · Revenue per Customer Segment  (by customer type)",
    """
    SELECT
        c.customer_type                         AS segment,
        COUNT(DISTINCT b.customer_id)           AS customers,
        COUNT(*)                                AS bookings,
        ROUND(SUM(i.net_amount), 2)             AS total_net_revenue,
        ROUND(AVG(i.net_amount), 2)             AS avg_revenue_per_booking
    FROM Booking  b
    JOIN Customer c ON c.customer_id = b.customer_id
    JOIN Invoice  i ON i.booking_id  = b.booking_id
    WHERE b.booking_status = 'completed'
    GROUP BY c.customer_type
    ORDER BY total_net_revenue DESC
    """
)

run(
    "KPI 5b · Revenue per Customer Segment  (by nationality, top 10)",
    """
    SELECT
        c.nationality                           AS nationality,
        COUNT(DISTINCT b.customer_id)           AS customers,
        COUNT(*)                                AS bookings,
        ROUND(SUM(i.net_amount), 2)             AS total_net_revenue,
        ROUND(AVG(i.net_amount), 2)             AS avg_revenue_per_booking
    FROM Booking  b
    JOIN Customer c ON c.customer_id = b.customer_id
    JOIN Invoice  i ON i.booking_id  = b.booking_id
    WHERE b.booking_status = 'completed'
    GROUP BY c.nationality
    ORDER BY total_net_revenue DESC
    LIMIT 10
    """
)

run(
    "KPI 5c · Revenue per Customer Segment  (by cumulative spend tier)",
    """
    WITH spend AS (
        SELECT b.customer_id, SUM(i.net_amount) AS total_spend
        FROM Booking b
        JOIN Invoice i ON i.booking_id = b.booking_id
        WHERE b.booking_status = 'completed'
        GROUP BY b.customer_id
    ),
    tiered AS (
        SELECT customer_id, total_spend,
               CASE
                   WHEN total_spend >= 10000 THEN '3_High (>=10k)'
                   WHEN total_spend >=  3000 THEN '2_Mid  (3k-10k)'
                   ELSE                           '1_Low  (<3k)'
               END AS spend_tier
        FROM spend
    )
    SELECT
        spend_tier,
        COUNT(*)                                AS customers,
        ROUND(SUM(total_spend), 2)              AS total_revenue,
        ROUND(AVG(total_spend), 2)              AS avg_revenue_per_customer
    FROM tiered
    GROUP BY spend_tier
    ORDER BY spend_tier DESC
    """
)


# ── KPI 6 — AVERAGE DAILY RATE (ADR) ─────────────────────────────────────────
run(
    "KPI 6 · Average Daily Rate  (by brand segment, per year)",
    """
    SELECT
        br.segment                                                   AS brand_segment,
        strftime('%Y', b.check_in)                                   AS year,
        COUNT(*)                                                     AS bookings,
        ROUND(AVG(
            b.listed_price / (julianday(b.check_out) - julianday(b.check_in))
        ), 2)                                                        AS adr
    FROM Booking b
    JOIN Room   r  ON r.room_id   = b.room_id
    JOIN Hotel  h  ON h.hotel_id  = r.hotel_id
    JOIN Brands br ON br.brand_id = h.brand_id
    WHERE b.booking_status IN ('confirmed', 'completed')
    GROUP BY br.segment, year
    ORDER BY brand_segment, year
    """
)

run(
    "KPI 6b · Average Daily Rate  (by room type)",
    """
    SELECT
        r.room_type                                                  AS room_type,
        COUNT(*)                                                     AS bookings,
        ROUND(AVG(
            b.listed_price / (julianday(b.check_out) - julianday(b.check_in))
        ), 2)                                                        AS adr,
        ROUND(MIN(
            b.listed_price / (julianday(b.check_out) - julianday(b.check_in))
        ), 2)                                                        AS min_adr,
        ROUND(MAX(
            b.listed_price / (julianday(b.check_out) - julianday(b.check_in))
        ), 2)                                                        AS max_adr
    FROM Booking b
    JOIN Room r ON r.room_id = b.room_id
    WHERE b.booking_status IN ('confirmed', 'completed')
    GROUP BY r.room_type
    ORDER BY adr DESC
    """
)


# ── KPI 7 — DISCOUNT UPTAKE RATE ─────────────────────────────────────────────
# Only counts modifiers that REDUCE the price (discount_value < 0).
# Surcharges like "Temporada Alta Verano" or "Cargo Limpieza Sol" don't count
# as customer-facing discounts and are excluded here.
run(
    "KPI 7 · Discount Uptake Rate  (by customer type and membership status)",
    """
    WITH flagged AS (
        SELECT
            b.booking_id,
            c.customer_type,
            CASE WHEN mr.customer_id IS NOT NULL THEN 'Member' ELSE 'Non-member' END
                AS membership,
            CASE WHEN SUM(CASE WHEN bm.discount_value < 0 THEN 1 ELSE 0 END) > 0
                 THEN 1 ELSE 0 END
                AS has_discount
        FROM Booking b
        JOIN Customer c ON c.customer_id = b.customer_id
        LEFT JOIN MeliaRewards mr      ON mr.customer_id = b.customer_id
        LEFT JOIN BookingModifier bm   ON bm.booking_id  = b.booking_id
        WHERE b.booking_status IN ('confirmed', 'completed')
        GROUP BY b.booking_id, c.customer_type, membership
    )
    SELECT
        customer_type,
        membership,
        COUNT(*)                                          AS total_bookings,
        SUM(has_discount)                                 AS bookings_with_discount,
        ROUND(100.0 * SUM(has_discount) / COUNT(*), 1)   AS uptake_rate_pct
    FROM flagged
    GROUP BY customer_type, membership
    ORDER BY customer_type, membership
    """
)

run(
    "KPI 7b · Top modifiers by usage (discounts and surcharges, separately)",
    """
    SELECT
        pm.name                                           AS modifier_name,
        pm.modifier_type,
        CASE WHEN pm.value < 0 THEN 'discount' ELSE 'surcharge' END AS effect,
        COUNT(DISTINCT bm.booking_id)                     AS bookings_using_modifier,
        ROUND(100.0 * COUNT(DISTINCT bm.booking_id)
              / (SELECT COUNT(*) FROM Booking
                 WHERE booking_status IN ('confirmed','completed')), 2)
                                                          AS pct_of_all_bookings
    FROM BookingModifier bm
    JOIN PriceModifier pm ON pm.modifier_id = bm.modifier_id
    JOIN Booking b        ON b.booking_id  = bm.booking_id
    WHERE b.booking_status IN ('confirmed', 'completed')
    GROUP BY pm.modifier_id, pm.name, pm.modifier_type, effect
    ORDER BY bookings_using_modifier DESC
    LIMIT 15
    """
)


# ── KPI 8 — CHURN RISK INDEX ──────────────────────────────────────────────────
run(
    "KPI 8 · Churn Risk Index  (by loyalty tier, reference date = today)",
    """
    WITH last_stay AS (
        SELECT
            mr.customer_id,
            mr.points_balance,
            CASE
                WHEN mr.points_balance >= 30000 THEN 'Platinum'
                WHEN mr.points_balance >= 15000 THEN 'Gold'
                WHEN mr.points_balance >= 5000  THEN 'Silver'
                ELSE                                 'Basic'
            END AS loyalty_tier,
            MAX(b.check_out) AS last_checkout
        FROM MeliaRewards mr
        LEFT JOIN Booking b ON b.customer_id = mr.customer_id
                           AND b.booking_status = 'completed'
        GROUP BY mr.customer_id
    ),
    flagged AS (
        SELECT
            loyalty_tier,
            CASE
                WHEN last_checkout IS NULL THEN 1
                WHEN julianday('now') - julianday(last_checkout) > 365 THEN 1
                ELSE 0
            END AS at_risk
        FROM last_stay
    )
    SELECT
        loyalty_tier,
        COUNT(*)                                          AS total_members,
        SUM(at_risk)                                      AS at_risk_members,
        ROUND(100.0 * SUM(at_risk) / COUNT(*), 1)         AS churn_risk_pct
    FROM flagged
    GROUP BY loyalty_tier
    ORDER BY CASE loyalty_tier
        WHEN 'Platinum' THEN 1
        WHEN 'Gold'     THEN 2
        WHEN 'Silver'   THEN 3
        ELSE 4 END
    """
)

run(
    "KPI 8b · Churn Risk Index  (at-risk members grouped by tier)",
    """
    WITH last_stay AS (
        SELECT
            mr.customer_id,
            mr.points_balance,
            CASE
                WHEN mr.points_balance >= 30000 THEN 'Platinum'
                WHEN mr.points_balance >= 15000 THEN 'Gold'
                WHEN mr.points_balance >= 5000  THEN 'Silver'
                ELSE                                 'Basic'
            END AS loyalty_tier,
            MAX(b.check_out) AS last_checkout
        FROM MeliaRewards mr
        LEFT JOIN Booking b ON b.customer_id = mr.customer_id
                           AND b.booking_status = 'completed'
        GROUP BY mr.customer_id
    ),
    at_risk AS (
        SELECT
            loyalty_tier,
            CASE WHEN last_checkout IS NULL THEN 'never stayed' ELSE 'inactive 12m+' END
                AS risk_reason,
            ROUND(julianday('now') - julianday(COALESCE(last_checkout,'2020-01-01')), 0)
                AS days_inactive
        FROM last_stay
        WHERE last_checkout IS NULL
           OR julianday('now') - julianday(last_checkout) > 365
    )
    SELECT
        loyalty_tier,
        risk_reason,
        COUNT(*)                              AS at_risk_members,
        ROUND(AVG(days_inactive), 0)          AS avg_days_inactive,
        ROUND(MIN(days_inactive), 0)          AS min_days_inactive,
        ROUND(MAX(days_inactive), 0)          AS max_days_inactive
    FROM at_risk
    GROUP BY loyalty_tier, risk_reason
    ORDER BY CASE loyalty_tier
        WHEN 'Platinum' THEN 1
        WHEN 'Gold'     THEN 2
        WHEN 'Silver'   THEN 3
        ELSE 4 END, risk_reason
    """
)

# ── KPI 9 — CANCELLATION RATE  (NEW) ─────────────────────────────────────────
# v4 makes cancellations a real first-class state with proper side-effects
# (RoomDay freed, Invoice refunded, points clawed back). Now we can measure
# them properly.
run(
    "KPI 9 · Cancellation Rate  (by customer type and channel)",
    """
    SELECT
        c.customer_type                                   AS customer_type,
        b.booking_channel                                 AS channel,
        COUNT(*)                                          AS total_bookings,
        SUM(CASE WHEN b.booking_status = 'cancelled' THEN 1 ELSE 0 END)
                                                          AS cancellations,
        ROUND(100.0 *
              SUM(CASE WHEN b.booking_status = 'cancelled' THEN 1 ELSE 0 END)
              / COUNT(*), 1)                              AS cancellation_rate_pct
    FROM Booking b
    JOIN Customer c ON c.customer_id = b.customer_id
    GROUP BY c.customer_type, b.booking_channel
    ORDER BY cancellation_rate_pct DESC
    """
)

run(
    "KPI 9b · Cancellation Rate  (overall + by brand segment)",
    """
    SELECT
        br.segment                                        AS brand_segment,
        COUNT(*)                                          AS total_bookings,
        SUM(CASE WHEN b.booking_status = 'cancelled' THEN 1 ELSE 0 END)
                                                          AS cancellations,
        ROUND(100.0 *
              SUM(CASE WHEN b.booking_status = 'cancelled' THEN 1 ELSE 0 END)
              / COUNT(*), 1)                              AS cancellation_rate_pct
    FROM Booking  b
    JOIN Room     r  ON r.room_id   = b.room_id
    JOIN Hotel    h  ON h.hotel_id  = r.hotel_id
    JOIN Brands   br ON br.brand_id = h.brand_id
    GROUP BY br.segment
    ORDER BY cancellation_rate_pct DESC
    """
)


# ── KPI 10 — OCCUPANCY RATE  (NEW) ───────────────────────────────────────────
# Reads from RoomDay.status, which is now reliably maintained by the trigger
# system (occupied on confirmed/completed insert, freed on cancel).
run(
    "KPI 10 · Occupancy Rate  (by year and brand segment)",
    """
    SELECT
        strftime('%Y', rd.day)                            AS year,
        br.segment                                        AS brand_segment,
        COUNT(*)                                          AS total_room_nights,
        SUM(CASE WHEN rd.status = 'occupied' THEN 1 ELSE 0 END)
                                                          AS occupied_nights,
        ROUND(100.0 *
              SUM(CASE WHEN rd.status = 'occupied' THEN 1 ELSE 0 END)
              / COUNT(*), 1)                              AS occupancy_rate_pct
    FROM RoomDay rd
    JOIN Room    r  ON r.room_id   = rd.room_id
    JOIN Hotel   h  ON h.hotel_id  = r.hotel_id
    JOIN Brands  br ON br.brand_id = h.brand_id
    GROUP BY year, brand_segment
    ORDER BY year, brand_segment
    """
)

run(
    "KPI 10b · Occupancy Rate  (top 10 hotels by occupancy)",
    """
    SELECT
        h.name                                            AS hotel,
        br.segment                                        AS segment,
        COUNT(*)                                          AS total_room_nights,
        SUM(CASE WHEN rd.status = 'occupied' THEN 1 ELSE 0 END)
                                                          AS occupied_nights,
        ROUND(100.0 *
              SUM(CASE WHEN rd.status = 'occupied' THEN 1 ELSE 0 END)
              / COUNT(*), 1)                              AS occupancy_rate_pct
    FROM RoomDay rd
    JOIN Room    r  ON r.room_id   = rd.room_id
    JOIN Hotel   h  ON h.hotel_id  = r.hotel_id
    JOIN Brands  br ON br.brand_id = h.brand_id
    GROUP BY h.hotel_id, h.name, br.segment
    ORDER BY occupancy_rate_pct DESC
    LIMIT 10
    """
)


# ── KPI 11 — REVENUE LOST TO CANCELLATIONS  (NEW) ────────────────────────────
# What would have been earned from cancelled bookings if they had completed.
# Uses Invoice.net_amount (post-discount, pre-tax) of refunded invoices.
run(
    "KPI 11 · Revenue Lost to Cancellations  (by year and brand segment)",
    """
    SELECT
        strftime('%Y', b.check_in)                        AS year,
        br.segment                                        AS brand_segment,
        COUNT(*)                                          AS cancellations,
        ROUND(SUM(i.net_amount), 2)                       AS net_revenue_lost,
        ROUND(AVG(i.net_amount), 2)                       AS avg_lost_per_cancellation
    FROM Booking b
    JOIN Invoice i  ON i.booking_id  = b.booking_id
    JOIN Room    r  ON r.room_id     = b.room_id
    JOIN Hotel   h  ON h.hotel_id    = r.hotel_id
    JOIN Brands  br ON br.brand_id   = h.brand_id
    WHERE b.booking_status = 'cancelled'
    GROUP BY year, brand_segment
    ORDER BY year, net_revenue_lost DESC
    """
)


print("\n" + "=" * 62)
print("  All KPI queries complete.")
print("=" * 62)

conn.close()
