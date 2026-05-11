"""
MELIA HOTELS — Business Analytics Dashboard
Run with:  streamlit run streamlit_app.py
Requires:  pip install streamlit pandas plotly
"""

import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import subprocess
import sys
from pathlib import Path

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Meliá Analytics",
    page_icon="🏨",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path(__file__).parent / "melia.db"

# ── Auto-setup: generate DB on first launch if it doesn't exist ───────────────
if not DB_PATH.exists():
    with st.spinner("⚙️ First launch — building database (this takes ~2 minutes)..."):
        base = Path(__file__).parent
        subprocess.run([sys.executable, str(base / "01_schema.py")], check=True)
        subprocess.run([sys.executable, str(base / "02_data.py")],   check=True)
    st.success("✅ Database ready!")
    st.rerun()

# ── Colour palette ────────────────────────────────────────────────────────────
SEGMENT_COLORS = {"Luxury": "#1B3A6B", "Premium": "#C9A84C", "Essential": "#5B8DB8"}
CHANNEL_COLORS = {"direct": "#1B3A6B", "agency": "#C9A84C", "web": "#5B8DB8", "app": "#7DC4A0"}
TIER_COLORS    = {"Platinum": "#7B5EA7", "Gold": "#C9A84C", "Silver": "#8C9EA6", "Basic": "#5B8DB8"}
TYPE_COLORS    = {"direct": "#1B3A6B", "agency": "#C9A84C"}
MEM_COLORS     = {"Member": "#1B3A6B", "Non-member": "#C9A84C"}


# ── DB helpers ────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def run_query(sql: str) -> pd.DataFrame:
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(sql, conn)
    conn.close()
    return df


# ── Load all data once ────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_all():
    bookings = run_query("""
        SELECT
            b.booking_id,
            b.customer_id,
            c.customer_type,
            c.nationality,
            b.booking_date,
            b.check_in,
            b.check_out,
            b.booking_status,
            b.booking_channel,
            b.listed_price,
            b.guests,
            r.room_type,
            h.name          AS hotel_name,
            br.segment      AS brand_segment,
            i.net_amount,
            i.total_paid    AS invoice_total,
            CASE WHEN mr.customer_id IS NOT NULL THEN 'Member' ELSE 'Non-member' END
                            AS membership,
            CASE
                WHEN mr.points_balance >= 30000 THEN 'Platinum'
                WHEN mr.points_balance >= 15000 THEN 'Gold'
                WHEN mr.points_balance >= 5000  THEN 'Silver'
                WHEN mr.customer_id IS NOT NULL THEN 'Basic'
                ELSE NULL
            END             AS loyalty_tier,
            b.points_earned,
            ROUND(julianday(b.check_out) - julianday(b.check_in), 0)          AS nights,
            ROUND(julianday(b.check_in)  - julianday(b.booking_date), 0)       AS lead_time,
            strftime('%Y',    b.check_in)   AS year,
            strftime('%Y-%m', b.check_in)   AS year_month
        FROM Booking b
        JOIN Customer      c  ON c.customer_id  = b.customer_id
        JOIN Room          r  ON r.room_id      = b.room_id
        JOIN Hotel         h  ON h.hotel_id     = r.hotel_id
        JOIN Brands        br ON br.brand_id    = h.brand_id
        JOIN Invoice       i  ON i.booking_id   = b.booking_id
        LEFT JOIN MeliaRewards mr ON mr.customer_id = b.customer_id
    """)

    members = run_query("""
        SELECT
            mr.customer_id,
            c.name,
            c.nationality,
            mr.points_balance,
            mr.join_date,
            CASE
                WHEN mr.points_balance >= 30000 THEN 'Platinum'
                WHEN mr.points_balance >= 15000 THEN 'Gold'
                WHEN mr.points_balance >= 5000  THEN 'Silver'
                ELSE 'Basic'
            END AS loyalty_tier,
            MAX(b.check_out) AS last_checkout
        FROM MeliaRewards mr
        JOIN Customer c ON c.customer_id = mr.customer_id
        LEFT JOIN Booking b ON b.customer_id = mr.customer_id
                           AND b.booking_status = 'completed'
        GROUP BY mr.customer_id
    """)

    discounts = run_query("""
        SELECT
            pm.name         AS modifier_name,
            pm.modifier_type,
            CASE WHEN pm.value < 0 THEN 'discount' ELSE 'surcharge' END AS effect,
            COUNT(DISTINCT bm.booking_id) AS bookings_using
        FROM BookingModifier bm
        JOIN PriceModifier pm ON pm.modifier_id = bm.modifier_id
        JOIN Booking b ON b.booking_id = bm.booking_id
        WHERE b.booking_status IN ('confirmed','completed')
        GROUP BY pm.modifier_id, pm.name, pm.modifier_type, effect
        ORDER BY bookings_using DESC
        LIMIT 10
    """)

    # IDs of bookings that received an actual DISCOUNT (negative value).
    # Surcharges like Temporada Alta or Cargo Limpieza Sol don't count here.
    discounted_ids = run_query("""
        SELECT DISTINCT booking_id FROM BookingModifier
        WHERE discount_value < 0
    """)["booking_id"].tolist()

    occupancy = run_query("""
        SELECT
            strftime('%Y',    rd.day)              AS year,
            strftime('%Y-%m', rd.day)              AS year_month,
            br.segment                             AS brand_segment,
            h.name                                 AS hotel_name,
            COUNT(*)                               AS room_nights,
            SUM(CASE WHEN rd.status = 'occupied' THEN 1 ELSE 0 END)
                                                   AS occupied_nights
        FROM   RoomDay rd
        JOIN   Room    r  ON r.room_id   = rd.room_id
        JOIN   Hotel   h  ON h.hotel_id  = r.hotel_id
        JOIN   Brands  br ON br.brand_id = h.brand_id
        GROUP  BY year, year_month, br.segment, h.name
    """)

    return bookings, members, discounts, discounted_ids, occupancy


df, members, discounts, discounted_ids, occupancy = load_all()

# Derived columns
df["year"]        = df["year"].astype(str)
df["adr"]         = (df["listed_price"] / df["nights"]).round(2)
df["has_discount"] = df["booking_id"].isin(discounted_ids).astype(int)

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("🏨 Meliá Analytics")
st.sidebar.markdown("---")
st.sidebar.subheader("Filters")

years       = sorted(df["year"].unique().tolist())
sel_years   = st.sidebar.multiselect("Year", years, default=years)

segments    = sorted(df["brand_segment"].unique().tolist())
sel_segs    = st.sidebar.multiselect("Brand segment", segments, default=segments)

channels    = sorted(df["booking_channel"].unique().tolist())
sel_chan    = st.sidebar.multiselect("Booking channel", channels, default=channels)

cust_types  = sorted(df["customer_type"].unique().tolist())
sel_ctype   = st.sidebar.multiselect("Customer type", cust_types, default=cust_types)

# Apply filters
mask = (
    df["year"].isin(sel_years) &
    df["brand_segment"].isin(sel_segs) &
    df["booking_channel"].isin(sel_chan) &
    df["customer_type"].isin(sel_ctype)
)
fdf         = df[mask]
f_completed = fdf[fdf["booking_status"] == "completed"]
f_active    = fdf[fdf["booking_status"].isin(["confirmed", "completed"])]

st.sidebar.markdown("---")
st.sidebar.caption(f"**{len(f_active):,}** bookings match current filters.")


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🏨 Meliá Hotels — Business Analytics")
st.caption("Customer Behaviour & Profiling · Data: 2025–2027 · 20 hotels · 1,000 rooms")
st.markdown("---")


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊  Overview",
    "📅  Booking Habits",
    "💶  Revenue",
    "⭐  Loyalty",
    "🛠️  Operations",
])


# ════════════════════════════════════════════════════════════════════════════
#  TAB 1 — OVERVIEW
# ════════════════════════════════════════════════════════════════════════════
with tab1:

    # ── KPI cards ──
    n_total      = len(fdf)
    n_cancelled  = (fdf["booking_status"] == "cancelled").sum()
    cancel_rate  = (n_cancelled / n_total * 100) if n_total else 0.0
    occ_filt_overview = occupancy[
        occupancy["brand_segment"].isin(sel_segs) &
        occupancy["year"].isin(sel_years)
    ]
    occupied_n   = occ_filt_overview["occupied_nights"].sum()
    total_n      = occ_filt_overview["room_nights"].sum()
    occ_rate     = (occupied_n / total_n * 100) if total_n else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Active bookings",     f"{len(f_active):,}")
    c2.metric("Completed stays",     f"{len(f_completed):,}")
    c3.metric("Net revenue",         f"€{f_completed['net_amount'].sum():,.0f}")
    c4.metric("Avg ADR",             f"€{f_active['adr'].mean():,.2f}")
    c5.metric("Cancellation rate",   f"{cancel_rate:.1f}%")
    c6.metric("Occupancy rate",      f"{occ_rate:.1f}%")

    st.markdown("---")

    # ── Monthly revenue trend ──
    monthly = (
        f_completed
        .groupby("year_month")["net_amount"].sum()
        .reset_index()
        .rename(columns={"year_month": "Month", "net_amount": "Net Revenue (€)"})
    )
    fig = px.area(
        monthly, x="Month", y="Net Revenue (€)",
        title="Monthly Net Revenue",
        color_discrete_sequence=["#1B3A6B"],
    )
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    col1, col2 = st.columns(2)

    # ── Bookings by channel (donut) ──
    ch = f_active.groupby("booking_channel").size().reset_index(name="Bookings")
    fig2 = px.pie(
        ch, names="booking_channel", values="Bookings",
        title="Bookings by Channel",
        color="booking_channel", color_discrete_map=CHANNEL_COLORS,
        hole=0.45,
    )
    fig2.update_traces(textposition="outside", textinfo="label+percent")
    col1.plotly_chart(fig2, use_container_width=True)

    # ── Bookings by brand segment (donut) ──
    seg = f_active.groupby("brand_segment").size().reset_index(name="Bookings")
    fig3 = px.pie(
        seg, names="brand_segment", values="Bookings",
        title="Bookings by Brand Segment",
        color="brand_segment", color_discrete_map=SEGMENT_COLORS,
        hole=0.45,
    )
    fig3.update_traces(textposition="outside", textinfo="label+percent")
    col2.plotly_chart(fig3, use_container_width=True)

    # ── Status breakdown table ──
    st.markdown("**Booking status breakdown**")
    status_df = (
        fdf.groupby("booking_status").size()
        .reset_index(name="Count")
        .rename(columns={"booking_status": "Status"})
    )
    status_df["Share (%)"] = (status_df["Count"] / status_df["Count"].sum() * 100).round(1)
    st.dataframe(status_df, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════════════════
#  TAB 2 — BOOKING HABITS
# ════════════════════════════════════════════════════════════════════════════
with tab2:

    col1, col2 = st.columns(2)

    # ── KPI 1 — Lead time by channel ──
    lead = (
        f_active.groupby("booking_channel")["lead_time"]
        .mean().round(1).reset_index()
        .rename(columns={"booking_channel": "Channel", "lead_time": "Avg Lead Time (days)"})
        .sort_values("Avg Lead Time (days)", ascending=False)
    )
    fig = px.bar(
        lead, x="Channel", y="Avg Lead Time (days)",
        title="KPI 1 — Avg Booking Lead Time by Channel",
        color="Channel", color_discrete_map=CHANNEL_COLORS,
        text_auto=".1f",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(showlegend=False, yaxis_range=[0, lead["Avg Lead Time (days)"].max() * 1.2])
    col1.plotly_chart(fig, use_container_width=True)

    # ── KPI 2 — Length of stay by brand segment ──
    los = (
        f_active.groupby("brand_segment")["nights"]
        .mean().round(1).reset_index()
        .rename(columns={"brand_segment": "Segment", "nights": "Avg Nights"})
        .sort_values("Avg Nights", ascending=False)
    )
    fig2 = px.bar(
        los, x="Segment", y="Avg Nights",
        title="KPI 2 — Avg Length of Stay by Brand Segment",
        color="Segment", color_discrete_map=SEGMENT_COLORS,
        text_auto=".1f",
    )
    fig2.update_traces(textposition="outside")
    fig2.update_layout(showlegend=False, yaxis_range=[0, los["Avg Nights"].max() * 1.25])
    col2.plotly_chart(fig2, use_container_width=True)

    # ── Lead time distribution ──
    fig3 = px.histogram(
        f_active, x="lead_time", nbins=30,
        color="booking_channel", color_discrete_map=CHANNEL_COLORS,
        title="Lead Time Distribution by Channel",
        labels={"lead_time": "Days before check-in", "count": "Bookings"},
        barmode="overlay", opacity=0.7,
    )
    st.plotly_chart(fig3, use_container_width=True)

    # ── Avg nights by room type ──
    rt = (
        f_active.groupby("room_type")["nights"]
        .mean().round(1).reset_index()
        .rename(columns={"room_type": "Room Type", "nights": "Avg Nights"})
        .sort_values("Avg Nights", ascending=False)
    )
    fig4 = px.bar(
        rt, x="Room Type", y="Avg Nights",
        title="Avg Length of Stay by Room Type",
        color_discrete_sequence=["#1B3A6B"],
        text_auto=".1f",
    )
    fig4.update_traces(textposition="outside")
    fig4.update_layout(yaxis_range=[0, rt["Avg Nights"].max() * 1.2])
    st.plotly_chart(fig4, use_container_width=True)

    # ── KPI 3 — Return rate ──
    stayed = (
        f_completed.groupby(["customer_id", "customer_type"])
        .size().reset_index(name="completed_stays")
    )
    return_rate = (
        stayed.groupby("customer_type")
        .apply(lambda g: pd.Series({
            "customers_who_stayed": len(g),
            "returning_customers":  (g["completed_stays"] > 1).sum(),
        }))
        .reset_index()
    )
    return_rate["return_rate_pct"] = (
        return_rate["returning_customers"] / return_rate["customers_who_stayed"] * 100
    ).round(1)

    col5, col6 = st.columns(2)
    fig5 = px.bar(
        return_rate, x="customer_type", y="return_rate_pct",
        title="KPI 3 — Return Rate by Customer Type (%)",
        color="customer_type", color_discrete_map=TYPE_COLORS,
        text_auto=".1f",
        labels={"customer_type": "Customer Type", "return_rate_pct": "Return Rate (%)"},
    )
    fig5.update_traces(textposition="outside")
    fig5.update_layout(showlegend=False, yaxis_range=[0, 100])
    col5.plotly_chart(fig5, use_container_width=True)

    overall_stayed     = len(stayed)
    overall_returning  = (stayed["completed_stays"] > 1).sum()
    overall_rate       = overall_returning / overall_stayed * 100 if overall_stayed else 0
    col6.metric("Overall return rate",    f"{overall_rate:.1f}%")
    col6.metric("Customers who stayed",   f"{overall_stayed:,}")
    col6.metric("Returning customers",    f"{int(overall_returning):,}")


# ════════════════════════════════════════════════════════════════════════════
#  TAB 3 — REVENUE
# ════════════════════════════════════════════════════════════════════════════
with tab3:

    col1, col2 = st.columns(2)

    # ── KPI 5a — Revenue by customer type ──
    rev_type = (
        f_completed.groupby("customer_type")["net_amount"].sum()
        .reset_index()
        .rename(columns={"customer_type": "Customer Type", "net_amount": "Net Revenue (€)"})
        .sort_values("Net Revenue (€)", ascending=False)
    )
    fig = px.bar(
        rev_type, x="Customer Type", y="Net Revenue (€)",
        title="KPI 5a — Net Revenue by Customer Type",
        color="Customer Type", color_discrete_map=TYPE_COLORS,
        text_auto=",.0f",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(showlegend=False,
                      yaxis_range=[0, rev_type["Net Revenue (€)"].max() * 1.2])
    col1.plotly_chart(fig, use_container_width=True)

    # ── Revenue by brand segment ──
    rev_seg = (
        f_completed.groupby("brand_segment")["net_amount"].sum()
        .reset_index()
        .rename(columns={"brand_segment": "Segment", "net_amount": "Net Revenue (€)"})
        .sort_values("Net Revenue (€)", ascending=False)
    )
    fig2 = px.bar(
        rev_seg, x="Segment", y="Net Revenue (€)",
        title="Net Revenue by Brand Segment",
        color="Segment", color_discrete_map=SEGMENT_COLORS,
        text_auto=",.0f",
    )
    fig2.update_traces(textposition="outside")
    fig2.update_layout(showlegend=False,
                       yaxis_range=[0, rev_seg["Net Revenue (€)"].max() * 1.2])
    col2.plotly_chart(fig2, use_container_width=True)

    # ── KPI 5b — Revenue by nationality (top 10) ──
    rev_nat = (
        f_completed.groupby("nationality")["net_amount"].sum()
        .reset_index()
        .rename(columns={"nationality": "Nationality", "net_amount": "Net Revenue (€)"})
        .sort_values("Net Revenue (€)", ascending=False)
        .head(10)
    )
    fig3 = px.bar(
        rev_nat, x="Nationality", y="Net Revenue (€)",
        title="KPI 5b — Net Revenue by Nationality (Top 10)",
        color_discrete_sequence=["#1B3A6B"],
        text_auto=",.0f",
    )
    fig3.update_traces(textposition="outside")
    fig3.update_layout(yaxis_range=[0, rev_nat["Net Revenue (€)"].max() * 1.2])
    st.plotly_chart(fig3, use_container_width=True)

    # ── KPI 6 — ADR by brand segment per year ──
    adr = (
        f_active.groupby(["year", "brand_segment"])["adr"]
        .mean().round(2).reset_index()
        .rename(columns={"year": "Year", "brand_segment": "Segment", "adr": "ADR (€/night)"})
    )
    fig4 = px.line(
        adr, x="Year", y="ADR (€/night)",
        color="Segment", color_discrete_map=SEGMENT_COLORS,
        title="KPI 6 — Average Daily Rate by Brand Segment per Year",
        markers=True, text="ADR (€/night)",
    )
    fig4.update_traces(textposition="top center")
    st.plotly_chart(fig4, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
#  TAB 4 — LOYALTY
# ════════════════════════════════════════════════════════════════════════════
with tab4:

    col1, col2 = st.columns(2)

    # ── KPI 7 — Discount uptake ──
    uptake = (
        f_active.groupby(["customer_type", "membership"])
        .agg(total=("booking_id", "count"), with_disc=("has_discount", "sum"))
        .reset_index()
    )
    uptake["uptake_pct"] = (uptake["with_disc"] / uptake["total"] * 100).round(1)
    uptake["label"]      = uptake["customer_type"] + " / " + uptake["membership"]

    fig = px.bar(
        uptake, x="label", y="uptake_pct",
        title="KPI 7 — Discount Uptake Rate (%)",
        color="membership", color_discrete_map=MEM_COLORS,
        text_auto=".1f",
        labels={"label": "", "uptake_pct": "Uptake (%)"},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(yaxis_range=[0, 115], showlegend=True)
    col1.plotly_chart(fig, use_container_width=True)

    # ── KPI 4 — Points accumulation by tier ──
    pts = (
        f_completed[f_completed["loyalty_tier"].notna()]
        .groupby("loyalty_tier")["points_earned"]
        .mean().round(0).reset_index()
        .rename(columns={"loyalty_tier": "Tier", "points_earned": "Avg pts / stay"})
    )
    fig2 = px.bar(
        pts, x="Tier", y="Avg pts / stay",
        title="KPI 4 — Avg Points Earned per Stay by Tier",
        color="Tier", color_discrete_map=TIER_COLORS,
        text_auto=",.0f",
    )
    fig2.update_traces(textposition="outside")
    fig2.update_layout(showlegend=False,
                       yaxis_range=[0, pts["Avg pts / stay"].max() * 1.25] if len(pts) else None)
    col2.plotly_chart(fig2, use_container_width=True)

    # ── Top modifiers by usage ──
    fig3 = px.bar(
        discounts, x="bookings_using", y="modifier_name",
        orientation="h",
        title="Top 10 Modifiers by Usage (discount vs surcharge)",
        color="effect",
        color_discrete_map={"discount": "#1B3A6B", "surcharge": "#C9A84C"},
        labels={"bookings_using": "Bookings", "modifier_name": "", "effect": "Effect"},
        text_auto=",",
    )
    fig3.update_layout(yaxis={"categoryorder": "total ascending"}, legend_title="Effect")
    st.plotly_chart(fig3, use_container_width=True)

    # ── KPI 8 — Churn risk ──
    st.markdown("---")
    st.markdown("**KPI 8 — Churn Risk Index**")
    st.caption("Members with no completed booking in the last 12 months (reference: today)")

    members = members.copy()
    _today_ts = pd.Timestamp.today().normalize()
    members["days_inactive"] = members["last_checkout"].apply(
        lambda x: (_today_ts - pd.Timestamp(x)).days
        if pd.notna(x) and x != "" else 9999
    )
    members["at_risk"] = members["days_inactive"] > 365

    churn = (
        members.groupby("loyalty_tier")
        .agg(total=("customer_id", "count"), at_risk=("at_risk", "sum"))
        .reset_index()
    )
    churn["churn_pct"] = (churn["at_risk"] / churn["total"] * 100).round(1)

    col3, col4 = st.columns(2)
    fig4 = px.bar(
        churn, x="loyalty_tier", y="churn_pct",
        title="Churn Risk by Loyalty Tier (%)",
        color="loyalty_tier", color_discrete_map=TIER_COLORS,
        text_auto=".1f",
        labels={"loyalty_tier": "Tier", "churn_pct": "Churn Risk (%)"},
    )
    fig4.update_traces(textposition="outside")
    fig4.update_layout(showlegend=False, yaxis_range=[0, 20])
    col3.plotly_chart(fig4, use_container_width=True)

    # Overall churn metric
    total_members = len(members)
    at_risk_total = members["at_risk"].sum()
    col4.metric("Total members",    total_members)
    col4.metric("At-risk members",  int(at_risk_total))
    col4.metric("Overall churn risk",
                f"{at_risk_total / total_members * 100:.1f}%" if total_members else "N/A")
    col4.caption(
        "ℹ️ 0% churn in this simulation reflects the high booking volume "
        "per customer in the synthetic dataset. The query logic is correct "
        "and would surface churn in a real deployment."
    )

    # ── Member table ──
    st.markdown("---")
    st.markdown("**Full Loyalty Member List**")
    st.dataframe(
        members[["name", "loyalty_tier", "points_balance",
                 "join_date", "last_checkout", "days_inactive", "at_risk"]]
        .sort_values("points_balance", ascending=False)
        .rename(columns={
            "name":           "Member",
            "loyalty_tier":   "Tier",
            "points_balance": "Points",
            "join_date":      "Joined",
            "last_checkout":  "Last Stay",
            "days_inactive":  "Days Inactive",
            "at_risk":        "Churn Risk",
        }),
        use_container_width=True,
        hide_index=True,
    )


# ════════════════════════════════════════════════════════════════════════════
#  TAB 5 — OPERATIONS  (NEW v4)
# ════════════════════════════════════════════════════════════════════════════
with tab5:
    st.markdown(
        "Operational view: cancellations and occupancy. "
        "All figures here come straight from the schema (Booking, Invoice, "
        "RoomDay) and reflect the side-effects of the trigger system."
    )

    # ── KPI 9 — Cancellation rate ─────────────────────────────────────────
    st.markdown("### Cancellation rate")

    col1, col2 = st.columns(2)

    cancel_by_chan = (
        fdf.assign(is_cancelled=(fdf["booking_status"] == "cancelled").astype(int))
        .groupby("booking_channel")
        .agg(total=("booking_id", "count"), cancelled=("is_cancelled", "sum"))
        .reset_index()
    )
    cancel_by_chan["rate_pct"] = (
        cancel_by_chan["cancelled"] / cancel_by_chan["total"] * 100
    ).round(1)

    fig = px.bar(
        cancel_by_chan, x="booking_channel", y="rate_pct",
        title="Cancellation Rate by Channel (%)",
        color="booking_channel", color_discrete_map=CHANNEL_COLORS,
        text_auto=".1f",
        labels={"booking_channel": "Channel", "rate_pct": "Cancellation Rate (%)"},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(showlegend=False,
                      yaxis_range=[0, max(cancel_by_chan["rate_pct"].max() * 1.3, 5)])
    col1.plotly_chart(fig, use_container_width=True)

    cancel_by_seg = (
        fdf.assign(is_cancelled=(fdf["booking_status"] == "cancelled").astype(int))
        .groupby("brand_segment")
        .agg(total=("booking_id", "count"), cancelled=("is_cancelled", "sum"))
        .reset_index()
    )
    cancel_by_seg["rate_pct"] = (
        cancel_by_seg["cancelled"] / cancel_by_seg["total"] * 100
    ).round(1)

    fig2 = px.bar(
        cancel_by_seg, x="brand_segment", y="rate_pct",
        title="Cancellation Rate by Brand Segment (%)",
        color="brand_segment", color_discrete_map=SEGMENT_COLORS,
        text_auto=".1f",
        labels={"brand_segment": "Segment", "rate_pct": "Cancellation Rate (%)"},
    )
    fig2.update_traces(textposition="outside")
    fig2.update_layout(showlegend=False,
                       yaxis_range=[0, max(cancel_by_seg["rate_pct"].max() * 1.3, 5)])
    col2.plotly_chart(fig2, use_container_width=True)

    # ── KPI 11 — Revenue lost to cancellations ─────────────────────────────
    cancelled_with_rev = fdf[fdf["booking_status"] == "cancelled"]
    if len(cancelled_with_rev):
        rev_lost = (
            cancelled_with_rev.groupby("year")["net_amount"].sum()
            .reset_index()
            .rename(columns={"year": "Year", "net_amount": "Revenue Lost (€)"})
            .sort_values("Year")
        )
        fig3 = px.bar(
            rev_lost, x="Year", y="Revenue Lost (€)",
            title="Revenue Lost to Cancellations (per year)",
            color_discrete_sequence=["#C9A84C"],
            text_auto=",.0f",
        )
        fig3.update_traces(textposition="outside")
        fig3.update_layout(yaxis_range=[0, rev_lost["Revenue Lost (€)"].max() * 1.2])
        st.plotly_chart(fig3, use_container_width=True)

    st.markdown("---")

    # ── KPI 10 — Occupancy heatmap (segment × month) ───────────────────────
    st.markdown("### Occupancy rate")

    occ_filt = occupancy[
        occupancy["brand_segment"].isin(sel_segs) &
        occupancy["year"].isin(sel_years)
    ]
    occ_grid = (
        occ_filt.groupby(["brand_segment", "year_month"])
        .agg(occupied=("occupied_nights", "sum"), total=("room_nights", "sum"))
        .reset_index()
    )
    occ_grid["occupancy_pct"] = (occ_grid["occupied"] / occ_grid["total"] * 100).round(1)

    pivot = occ_grid.pivot(
        index="brand_segment", columns="year_month", values="occupancy_pct"
    ).fillna(0)

    fig4 = px.imshow(
        pivot,
        title="Monthly Occupancy Heatmap by Brand Segment (%)",
        labels={"x": "Month", "y": "Segment", "color": "Occupancy %"},
        aspect="auto",
        color_continuous_scale="Blues",
    )
    fig4.update_layout(coloraxis_colorbar_title="%")
    st.plotly_chart(fig4, use_container_width=True)

    # ── Top hotels by occupancy ────────────────────────────────────────────
    top_hotels = (
        occupancy[
            occupancy["brand_segment"].isin(sel_segs) &
            occupancy["year"].isin(sel_years)
        ]
        .groupby(["hotel_name", "brand_segment"])
        .agg(occupied=("occupied_nights", "sum"), total=("room_nights", "sum"))
        .reset_index()
    )
    top_hotels["occupancy_pct"] = (
        top_hotels["occupied"] / top_hotels["total"] * 100
    ).round(1)
    top_hotels = top_hotels.sort_values("occupancy_pct", ascending=False).head(10)

    fig5 = px.bar(
        top_hotels, x="occupancy_pct", y="hotel_name",
        orientation="h",
        title="Top 10 Hotels by Occupancy",
        color="brand_segment", color_discrete_map=SEGMENT_COLORS,
        text_auto=".1f",
        labels={"occupancy_pct": "Occupancy %", "hotel_name": ""},
    )
    fig5.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig5, use_container_width=True)
