"""
MELIA HOTELS — Cancel a Booking
================================
Simple cancellation flow:

  1. Customer enters their email.
  2. The page lists every booking they CAN cancel, namely those with
        booking_status = 'confirmed'  AND  check_in >= today.
     Bookings that are 'completed', 'cancelled' or already in the past
     do not appear (the schema also blocks completed → cancelled via
     trg_block_completed_cancel for defence in depth).
  3. They click [Cancel] on a booking → a small dialog asks for a
     reason → on confirm, a single SQL UPDATE fires the schema trigger
     which frees RoomDay, refunds Invoice, and NULLs out points_earned.
"""

import streamlit as st
import sqlite3
import subprocess, sys
from pathlib import Path
from datetime import date

st.set_page_config(page_title="Cancel a Booking", page_icon="❌", layout="wide")

DB_PATH = Path(__file__).parent.parent / "melia.db"

if not DB_PATH.exists():
    with st.spinner("⚙️ Building database for first time (~2 min)..."):
        base = Path(__file__).parent.parent
        subprocess.run([sys.executable, str(base / "01_schema.py")], check=True)
        subprocess.run([sys.executable, str(base / "02_data.py")],   check=True)
    st.rerun()

REASONS = [
    "Change of travel plans",
    "Medical emergency",
    "Flight cancellation",
    "Work conflict",
    "Personal reasons",
    "Other...",
]


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def find_cancellable_bookings(email):
    """
    Return (customer_name | None, list of cancellable booking rows).
    Cancellable = 'confirmed' AND check_in strictly in the future.

    Why strictly in the future:
      - 'confirmed' AND check_in > today    → upcoming reservation, cancellable
      - 'confirmed' AND check_in <= today < check_out → in-progress stay,
        excluded from the list (the guest is already at the hotel)
      - 'confirmed' AND check_out <= today  → does not exist by construction:
        data.py and the form-app force such bookings to 'completed' at
        insert time, so we never see this case
      - 'completed' / 'cancelled'           → also blocked by the schema
        trigger trg_block_completed_cancel (defence in depth)
    """
    today = date.today().isoformat()
    with get_conn() as conn:
        cust = conn.execute(
            "SELECT customer_id, name FROM Customer WHERE email = ?",
            (email,)
        ).fetchone()
        if not cust:
            return None, []
        cid, cname = cust
        rows = conn.execute("""
            SELECT
                b.booking_id, b.check_in, b.check_out, b.guests, b.total_paid,
                r.room_number, r.room_type,
                h.name, h.city
            FROM   Booking b
            JOIN   Room    r ON r.room_id  = b.room_id
            JOIN   Hotel   h ON h.hotel_id = r.hotel_id
            WHERE  b.customer_id    = ?
              AND  b.booking_status = 'confirmed'
              AND  b.check_in       > ?
            ORDER BY b.check_in
        """, (cid, today)).fetchall()
    return cname, rows


def cancel_booking(booking_id, reason):
    """Single UPDATE — the schema triggers do all side-effects."""
    today = date.today().isoformat()
    with get_conn() as conn:
        try:
            conn.execute("""
                UPDATE Booking
                SET    booking_status      = 'cancelled',
                       cancellation_date   = ?,
                       cancellation_reason = ?
                WHERE  booking_id = ?
            """, (today, reason, booking_id))
            conn.commit()
            return True, None
        except sqlite3.Error as e:
            return False, str(e)


# ── Cancellation dialog ───────────────────────────────────────────────────────
@st.dialog("Confirm cancellation")
def cancel_dialog(booking_id, hotel_name, check_in, check_out):
    st.write(f"Cancel your booking at **{hotel_name}**?")
    st.caption(f"{check_in}  →  {check_out}")

    reason = st.selectbox("Reason", REASONS)
    final_reason = reason
    if reason == "Other...":
        custom = st.text_input("Specify your reason")
        final_reason = custom.strip()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Confirm cancellation", type="primary", use_container_width=True):
            if not final_reason or final_reason == "Other...":
                st.error("Please specify a reason.")
            else:
                ok, err = cancel_booking(booking_id, final_reason)
                if ok:
                    st.toast(f"Booking #{booking_id} cancelled ✅", icon="✅")
                    st.rerun()
                else:
                    st.error(f"Cancellation failed: {err}")
    with col2:
        if st.button("Back", use_container_width=True):
            st.rerun()


# ── UI ────────────────────────────────────────────────────────────────────────
st.title("❌ Cancel a Booking")
st.caption(
    "Enter the email associated with your booking. Only **upcoming, confirmed** "
    "bookings can be cancelled — past stays, in-progress stays and existing "
    "cancellations do not appear in the list."
)

st.session_state.setdefault("looked_up_email", "")

email = st.text_input("Email", placeholder="you@example.com")

if st.button("Find my bookings", type="primary"):
    if not email.strip():
        st.error("Please enter your email.")
    else:
        st.session_state.looked_up_email = email.strip()

# ── Show cancellable bookings if a lookup has been performed ─────────────────
if st.session_state.looked_up_email:
    cust_name, bookings = find_cancellable_bookings(st.session_state.looked_up_email)

    if cust_name is None:
        st.error("No customer found with that email.")
    else:
        st.divider()
        st.markdown(f"### Hi {cust_name}")

        if not bookings:
            st.info("You have no upcoming bookings to cancel.")
        else:
            st.markdown(
                f"You have **{len(bookings)}** booking{'s' if len(bookings) > 1 else ''} "
                f"you can cancel:"
            )

            for b in bookings:
                bid, ci, co, guests, total, rnum, rtype, hname, city = b
                nights = (date.fromisoformat(co) - date.fromisoformat(ci)).days

                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{hname}**  ·  {city}")
                        st.write(f"Room {rnum} — {rtype}")
                        st.write(
                            f"{ci}  →  {co}  "
                            f"({nights} nights · {guests} guest{'s' if guests > 1 else ''})"
                        )
                        st.write(f"Total paid: **€ {total:,.2f}**")
                    with col2:
                        st.write("")  # vertical spacing
                        if st.button("Cancel",
                                     key=f"cancel_btn_{bid}",
                                     type="primary",
                                     use_container_width=True):
                            cancel_dialog(bid, hname, ci, co)
