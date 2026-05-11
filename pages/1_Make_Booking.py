"""
MELIA HOTELS — Booking Form
============================
A simple end-to-end booking form following the standard hotel website flow:

  1. Search        check-in/out, guests, optional city filter
  2. Browse        list of available rooms with their listed_price
  3. Identify      existing customer (by email) or new customer
  4. Confirm       3-step price breakdown + payment method
  5. Done          full invoice + "Make another booking"

Design principles:
  - This file performs INSERTs only (Customer, MeliaRewards, Booking).
    All pricing math, BookingModifier rows, Invoice creation, RoomDay
    status updates and MeliaRewards point awards are handled by the
    triggers in schema.py. The form is just a "client" of the schema.
  - The price preview shown to the user is computed by querying the
    schema directly, mirroring exactly what the trigger will compute on
    confirmation. Same source of truth, no duplicated formulas.
  - The date picker is constrained to the calendar window read from
    v_calendar_days, so it is impossible to request a date the trigger
    would reject for being out of range.
"""

import streamlit as st
import sqlite3
import pandas as pd
import subprocess, sys
from pathlib import Path
from datetime import date, timedelta

st.set_page_config(page_title="Make a Booking", page_icon="📅", layout="wide")

DB_PATH = Path(__file__).parent.parent / "melia.db"

if not DB_PATH.exists():
    with st.spinner("⚙️ Building database for first time (~2 min)..."):
        base = Path(__file__).parent.parent
        subprocess.run([sys.executable, str(base / "01_schema.py")], check=True)
        subprocess.run([sys.executable, str(base / "02_data.py")],   check=True)
    st.rerun()


# ── DB helpers ────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@st.cache_data(ttl=60)
def calendar_bounds():
    """Read the official calendar window from v_calendar_days."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(day), MAX(day) FROM v_calendar_days"
        ).fetchone()
    return date.fromisoformat(row[0]), date.fromisoformat(row[1])


@st.cache_data(ttl=60)
def list_cities():
    with get_conn() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT city FROM Hotel ORDER BY city"
        ).fetchall()]


def search_available_rooms(check_in, check_out, guests, city):
    """Rooms with capacity >= guests AND every requested night still 'available'."""
    nights = (check_out - check_in).days
    sql = """
        SELECT
            r.room_id,
            r.room_number,
            r.room_type,
            r.max_guests,
            h.name      AS hotel_name,
            h.city,
            h.country,
            br.name     AS brand_name,
            br.segment,
            (SELECT SUM(rd.price_per_day)
             FROM   RoomDay rd
             WHERE  rd.room_id = r.room_id
               AND  rd.day >= ? AND rd.day < ?) AS listed_price,
            (SELECT COUNT(*)
             FROM   RoomDay rd
             WHERE  rd.room_id = r.room_id
               AND  rd.day >= ? AND rd.day < ?
               AND  rd.status = 'available') AS available_nights
        FROM   Room r
        JOIN   Hotel  h  ON h.hotel_id = r.hotel_id
        JOIN   Brands br ON br.brand_id = h.brand_id
        WHERE  r.max_guests >= ?
    """
    params = [check_in, check_out, check_in, check_out, guests]
    if city:
        sql += " AND h.city = ?"
        params.append(city)
    sql += " ORDER BY listed_price"

    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    df = df[df["available_nights"] == nights].drop(columns=["available_nights"])
    return df


def lookup_customer_by_email(email):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT c.customer_id, c.name, c.customer_type, c.nationality,
                   c.commission_rate, mr.points_balance
            FROM   Customer c
            LEFT   JOIN MeliaRewards mr ON mr.customer_id = c.customer_id
            WHERE  c.email = ?
        """, (email,)).fetchone()
    if not row:
        return None
    cid, name, ctype, nat, comm, pts = row
    return {
        "customer_id": cid, "name": name, "customer_type": ctype,
        "nationality": nat, "commission_rate": comm, "points_balance": pts,
    }


def create_customer(name, email, nationality, enroll_rewards):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO Customer (name, customer_type, email, nationality, commission_rate)
            VALUES (?, 'direct', ?, ?, 0.00)
        """, (name, email, nationality))
        new_id = cur.lastrowid
        if enroll_rewards:
            conn.execute("""
                INSERT INTO MeliaRewards (customer_id, points_balance, join_date)
                VALUES (?, 0, DATE('now'))
            """, (new_id,))
        conn.commit()
    return new_id


def compute_price_preview(room_id, check_in, check_out, customer_id):
    """
    Mirror of the trigger's pricing logic, query-by-query.
    Returns a dict with intermediate amounts so the UI can show the 3-step
    breakdown the user expects.
    """
    with get_conn() as conn:
        listed = conn.execute("""
            SELECT COALESCE(SUM(price_per_day), 0)
            FROM   RoomDay
            WHERE  room_id = ? AND day >= ? AND day < ?
        """, (room_id, check_in, check_out)).fetchone()[0]

        member_pct = conn.execute("""
            SELECT pm.modifier_id, pm.name, pm.value
            FROM   PriceModifier pm, MeliaRewards mr
            WHERE  mr.customer_id = ?
              AND  pm.modifier_type = 'percentage'
              AND  pm.min_points_required IS NOT NULL
              AND  mr.points_balance >= pm.min_points_required
              AND  ? BETWEEN pm.start_date AND pm.end_date
            ORDER  BY pm.min_points_required DESC LIMIT 1
        """, (customer_id, check_in)).fetchone()

        member_fixed = conn.execute("""
            SELECT pm.modifier_id, pm.name, pm.value
            FROM   PriceModifier pm
            JOIN   MeliaRewards  mr ON mr.customer_id = ?
            JOIN   Room          r  ON r.room_id      = ?
            WHERE  pm.modifier_type = 'fixed'
              AND  pm.min_points_required IS NOT NULL
              AND  mr.points_balance >= pm.min_points_required
              AND  ? BETWEEN pm.start_date AND pm.end_date
              AND  (pm.hotel_id IS NULL OR pm.hotel_id = r.hotel_id)
        """, (customer_id, room_id, check_in)).fetchall()

        commission = conn.execute(
            "SELECT commission_rate FROM Customer WHERE customer_id = ?",
            (customer_id,)
        ).fetchone()[0]

    after_pct      = listed * (1 + (member_pct[2] if member_pct else 0))
    after_fixed    = after_pct + sum(m[2] for m in member_fixed)
    after_comm     = after_fixed * (1 - commission)
    tax_amt        = after_comm * 0.10
    total          = after_comm + tax_amt

    return {
        "listed_price":   round(listed, 2),
        "member_pct":     member_pct,
        "member_fixed":   member_fixed,
        "commission":     commission,
        "after_pct":      round(after_pct, 2),
        "after_fixed":    round(after_fixed, 2),
        "net_amount":     round(after_comm, 2),
        "tax_amount":     round(tax_amt, 2),
        "total_paid":     round(total, 2),
    }


def create_booking(customer_id, room_id, check_in, check_out, guests, payment_method):
    """
    INSERT INTO Booking — the trigger handles BookingModifier, Invoice,
    RoomDay status, and MeliaRewards points. Then UPDATE the invoice's
    payment_method to whatever the user chose (the trigger defaults to 'card').
    """
    today        = date.today().isoformat()
    booking_date = today   # form enforces check_in >= today, so booking_date is always today
    with get_conn() as conn:
        try:
            cur = conn.execute("""
                INSERT INTO Booking
                (customer_id, room_id, booking_date, check_in, check_out,
                 booking_status, booking_channel, guests)
                VALUES (?, ?, ?, ?, ?, 'confirmed', 'web', ?)
            """, (customer_id, room_id, booking_date, check_in, check_out, guests))
            booking_id = cur.lastrowid
            conn.execute(
                "UPDATE Invoice SET payment_method = ? WHERE booking_id = ?",
                (payment_method, booking_id)
            )
            conn.commit()

            row = conn.execute("""
                SELECT b.booking_id, b.listed_price, b.total_paid, b.points_earned,
                       i.invoice_id, i.net_amount, i.tax_rate, i.tax_amount,
                       i.status, i.payment_method
                FROM   Booking b JOIN Invoice i ON i.booking_id = b.booking_id
                WHERE  b.booking_id = ?
            """, (booking_id,)).fetchone()
            return {
                "ok": True,
                "booking_id":     row[0],
                "listed_price":   row[1],
                "total_paid":     row[2],
                "points_earned":  row[3],
                "invoice_id":     row[4],
                "net_amount":     row[5],
                "tax_rate":       row[6],
                "tax_amount":     row[7],
                "status":         row[8],
                "payment_method": row[9],
            }
        except sqlite3.IntegrityError as e:
            return {"ok": False, "error": str(e)}


def tier_for(points):
    if points is None:                return None
    if points >= 30000:               return "Platinum"
    if points >= 15000:               return "Gold"
    if points >= 5000:                return "Silver"
    return "Basic"


# ── UI ────────────────────────────────────────────────────────────────────────
st.title("🏨 Make a Booking")

if "step" not in st.session_state:
    st.session_state.step = "search"
for key in ["search_results", "selected_room", "customer", "booking_result"]:
    st.session_state.setdefault(key, None)


def go_to(step):
    st.session_state.step = step
    st.rerun()


def reset():
    for k in ["search_results", "selected_room", "customer", "booking_result"]:
        st.session_state[k] = None
    st.session_state.step = "search"
    st.rerun()


# ── Step 1: Search ────────────────────────────────────────────────────────────
if st.session_state.step == "search":
    st.subheader("1 · Search availability")
    cal_min, cal_max = calendar_bounds()

    # Earliest valid check-in is today (or the calendar's start, whichever is later).
    # Prevents the customer from booking in the past via the form.
    today      = date.today()
    min_in     = max(cal_min, today)
    default_in = min_in
    default_out = min_in + timedelta(days=2)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        check_in = st.date_input("Check-in",
                                 min_value=min_in, max_value=cal_max - timedelta(days=1),
                                 value=default_in)
    with c2:
        check_out = st.date_input("Check-out",
                                  min_value=min_in + timedelta(days=1), max_value=cal_max,
                                  value=default_out)
    with c3:
        guests = st.number_input("Guests", min_value=1, max_value=8, value=2)
    with c4:
        cities = ["Any"] + list_cities()
        city = st.selectbox("City", cities)

    st.caption(f"Calendar window: {cal_min} → {cal_max}  ·  Bookable from {min_in} onwards.")

    if st.button("Search rooms", type="primary"):
        if check_out <= check_in:
            st.error("Check-out must be after check-in.")
        else:
            df = search_available_rooms(
                check_in, check_out, guests,
                None if city == "Any" else city
            )
            st.session_state.search_results = {
                "check_in": check_in, "check_out": check_out,
                "guests": guests, "rooms": df,
            }
            go_to("browse")


# ── Step 2: Browse ────────────────────────────────────────────────────────────
elif st.session_state.step == "browse":
    res = st.session_state.search_results
    nights = (res["check_out"] - res["check_in"]).days
    st.subheader(
        f"2 · Available rooms — {res['check_in']} → {res['check_out']} "
        f"({nights} nights, {res['guests']} guests)"
    )

    if st.button("← Modify search"):
        go_to("search")

    if res["rooms"].empty:
        st.warning("No rooms available for these dates and capacity. Try different dates.")
    else:
        st.caption(f"{len(res['rooms'])} rooms available, sorted by price.")
        for _, room in res["rooms"].iterrows():
            with st.container(border=True):
                col1, col2, col3 = st.columns([4, 2, 1])
                with col1:
                    st.markdown(
                        f"**{room['hotel_name']}** "
                        f"·  _{room['brand_name']}_ ({room['segment']})"
                    )
                    st.write(
                        f"Room {room['room_number']} — {room['room_type']} "
                        f"· max {room['max_guests']} guests "
                        f"· {room['city']}, {room['country']}"
                    )
                with col2:
                    st.metric("Listed price", f"€ {room['listed_price']:,.2f}",
                              f"{nights} nights")
                with col3:
                    st.write("")
                    if st.button("Select", key=f"select_{room['room_id']}",
                                 use_container_width=True):
                        st.session_state.selected_room = room.to_dict()
                        go_to("customer")


# ── Step 3: Customer ──────────────────────────────────────────────────────────
elif st.session_state.step == "customer":
    st.subheader("3 · Tell us who you are")
    if st.button("← Back to room selection"):
        go_to("browse")

    tab_existing, tab_new = st.tabs(["I have an account", "I'm new here"])

    with tab_existing:
        email = st.text_input("Email", key="_form_email_existing")
        if st.button("Look me up"):
            cust = lookup_customer_by_email(email.strip()) if email.strip() else None
            if cust:
                st.session_state.customer = cust
                go_to("confirm")
            else:
                st.error("No account found with that email.")

    with tab_new:
        name      = st.text_input("Full name",   key="_form_name_new")
        email_new = st.text_input("Email",       key="_form_email_new")
        nat       = st.text_input("Nationality", key="_form_nat_new")
        enroll    = st.checkbox(
            "Enroll in MeliáRewards (free, earn points on every stay)",
            value=True
        )
        if st.button("Create account & continue"):
            if not (name.strip() and email_new.strip() and nat.strip()):
                st.error("Please fill in all fields.")
            elif lookup_customer_by_email(email_new.strip()):
                st.error("That email is already registered. Use the other tab.")
            else:
                create_customer(name.strip(), email_new.strip(), nat.strip(), enroll)
                st.session_state.customer = lookup_customer_by_email(email_new.strip())
                go_to("confirm")


# ── Step 4: Confirm with breakdown ────────────────────────────────────────────
elif st.session_state.step == "confirm":
    st.subheader("4 · Review & confirm")
    if st.button("← Change customer"):
        go_to("customer")

    res  = st.session_state.search_results
    room = st.session_state.selected_room
    cust = st.session_state.customer
    nights = (res["check_out"] - res["check_in"]).days

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Stay**")
        st.write(f"Hotel: {room['hotel_name']} ({room['city']})")
        st.write(f"Room: {room['room_number']} — {room['room_type']}")
        st.write(f"Dates: {res['check_in']} → {res['check_out']} ({nights} nights)")
        st.write(f"Guests: {res['guests']}")
    with col2:
        st.markdown("**Customer**")
        st.write(f"Name: {cust['name']}")
        st.write(f"Type: {cust['customer_type']}")
        if cust.get("points_balance") is not None:
            tier = tier_for(cust["points_balance"])
            st.write(f"MeliáRewards: **{tier}** "
                     f"({cust['points_balance']:,} points)")
        else:
            st.write("MeliáRewards: not enrolled")

    st.divider()
    bd = compute_price_preview(
        room["room_id"], res["check_in"], res["check_out"], cust["customer_id"]
    )

    st.markdown("### Price breakdown")

    # Step 1
    st.markdown("**Step 1 · Listed price**  _(public rate, same for everyone)_")
    avg_per_night = bd["listed_price"] / nights if nights else 0
    st.write(
        f"&nbsp;&nbsp;&nbsp; {nights} nights × € {avg_per_night:,.2f} avg/night "
        f"=  **€ {bd['listed_price']:,.2f}**"
    )

    # Step 2
    st.markdown("**Step 2 · Personal adjustments**")
    any_adj = False
    if bd["member_pct"]:
        any_adj = True
        _, mname, mval = bd["member_pct"]
        amt = bd["listed_price"] * mval
        st.write(f"&nbsp;&nbsp;&nbsp; {mname} ({mval*100:+.0f}%) → **€ {amt:+,.2f}**")
    for _, mname, mval in bd["member_fixed"]:
        any_adj = True
        st.write(f"&nbsp;&nbsp;&nbsp; {mname} → **€ {mval:+,.2f}**")
    if bd["commission"] > 0:
        any_adj = True
        comm_amt = bd["after_fixed"] * (-bd["commission"])
        st.write(
            f"&nbsp;&nbsp;&nbsp; Agency commission "
            f"({bd['commission']*100:.0f}%) → **€ {comm_amt:,.2f}**"
        )
    if not any_adj:
        st.write("&nbsp;&nbsp;&nbsp; _No adjustments — standard rate._")
    st.write(f"&nbsp;&nbsp;&nbsp; Subtotal (net):  **€ {bd['net_amount']:,.2f}**")

    # Step 3
    st.markdown("**Step 3 · Tax**")
    st.write(f"&nbsp;&nbsp;&nbsp; VAT (10%) → **€ {bd['tax_amount']:,.2f}**")

    st.divider()
    st.markdown(f"## Total to pay: € {bd['total_paid']:,.2f}")

    payment_method = st.selectbox("Payment method", ["card", "transfer", "cash"])

    if st.button("Confirm Booking", type="primary"):
        result = create_booking(
            cust["customer_id"], room["room_id"],
            res["check_in"], res["check_out"], res["guests"],
            payment_method,
        )
        if result["ok"]:
            st.session_state.booking_result = result
            go_to("done")
        else:
            st.error(f"Booking rejected by the database:\n\n{result['error']}")
            st.info(
                "This usually means the room got booked by someone else "
                "between your search and your confirmation. Try a different "
                "room or different dates."
            )


# ── Step 5: Done ──────────────────────────────────────────────────────────────
elif st.session_state.step == "done":
    res  = st.session_state.search_results
    room = st.session_state.selected_room
    cust = st.session_state.customer
    r    = st.session_state.booking_result

    st.success("✅ Booking confirmed!")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 📅 Booking")
        st.write(f"**Booking ID:** `{r['booking_id']}`")
        st.write(f"**Hotel:** {room['hotel_name']} ({room['city']})")
        st.write(f"**Room:** {room['room_number']} — {room['room_type']}")
        st.write(f"**Dates:** {res['check_in']} → {res['check_out']}")
        st.write(f"**Guests:** {res['guests']}")
        st.write(f"**Customer:** {cust['name']}")
        st.write(f"**Booking status:** confirmed")

    with col2:
        st.markdown("### 🧾 Invoice")
        st.write(f"**Invoice ID:** `{r['invoice_id']}`")
        st.write(f"**Listed price:** € {r['listed_price']:,.2f}")
        st.write(f"**Net amount:** € {r['net_amount']:,.2f}")
        st.write(f"**Tax ({r['tax_rate']*100:.0f}%):** € {r['tax_amount']:,.2f}")
        st.markdown(f"**Total paid: € {r['total_paid']:,.2f}**")
        st.write(f"**Payment method:** {r['payment_method']}")
        st.write(f"**Status:** {r['status']}")
        if r["points_earned"]:
            st.write(f"**Points earned:** {r['points_earned']:,}  "
                     f"_(credited to your MeliáRewards balance)_")

    st.divider()
    if st.button("Make another booking", type="primary"):
        reset()
