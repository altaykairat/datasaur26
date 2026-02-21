"""Admin Dashboard — CSV upload, batch routing, live stats, AI toggle."""
import streamlit as st
import pandas as pd
import time

from database.connection import get_db
from database.models import Manager, Ticket, Office, User
from engine.router import TicketRouter


def render_admin():
    """Render the Admin Dashboard page."""
    st.markdown("## Admin Dashboard")
    st.caption("Upload tickets, run auto-routing, view system statistics")
    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

    tab_routing, tab_stats, tab_manage = st.tabs([
        "Batch Routing", "Statistics", "Management"
    ])

    with tab_routing:
        _render_batch_routing()

    with tab_stats:
        _render_statistics()

    with tab_manage:
        _render_management()


def _render_batch_routing():
    """CSV upload and auto-routing interface."""

    # AI Mode selector
    col1, col2 = st.columns([1, 2])
    with col1:
        ai_mode = st.radio(
            "AI Engine",
            ["DeepSeek", "Phi-4 Local"],
            index=0,
            help="DeepSeek = Cloud API. Phi-4 = Local via Ollama."
        )
    with col2:
        st.markdown(f"""<div class="info-banner">
            <strong>{"DeepSeek" if ai_mode == "DeepSeek" else "Phi-4 Local"}</strong> —
            {"Cloud-based API. Requires DEEPSEEK_API_KEY in .env" if ai_mode == "DeepSeek" else "Runs locally via Ollama. Ensure ollama serve is running and phi4 is pulled."}
        </div>""", unsafe_allow_html=True)

    mode_key = "deepseek" if ai_mode == "DeepSeek" else "phi4"

    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

    # File uploader
    uploaded_file = st.file_uploader(
        "Upload tickets CSV",
        type=["csv"],
        help="Expected: GUID клиента, Описание, Сегмент клиента, Населённый пункт, Область"
    )

    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
        except Exception:
            try:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, encoding="cp1251")
            except Exception as e:
                st.error(f"Failed to read CSV: {e}")
                return

        st.success(f"Loaded **{len(df)}** tickets")

        with st.expander("Preview data", expanded=False):
            st.dataframe(df.head(10), use_container_width=True, hide_index=True)

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        if st.button("Start Auto-Routing", use_container_width=True, type="primary"):
            _run_batch_routing(df, mode_key)


def _run_batch_routing(df: pd.DataFrame, ai_mode: str):
    """Execute batch routing with live progress."""
    total = len(df)
    st.markdown(f"**Processing {total} tickets...**")

    progress_bar = st.progress(0)
    status_text = st.empty()

    live_stats = {
        "processed": 0,
        "assigned": 0,
        "unassigned": 0,
        "by_city": {},
        "by_type": {},
        "by_sentiment": {},
    }

    def progress_callback(current, total, result):
        live_stats["processed"] = current
        if result["assigned_manager"] != "Unassigned":
            live_stats["assigned"] += 1
        else:
            live_stats["unassigned"] += 1

        city = result.get("routed_office", "Unknown")
        live_stats["by_city"][city] = live_stats["by_city"].get(city, 0) + 1

        ai_type = result.get("ai_type", "Unknown")
        live_stats["by_type"][ai_type] = live_stats["by_type"].get(ai_type, 0) + 1

        sentiment = result.get("ai_sentiment", "Unknown")
        live_stats["by_sentiment"][sentiment] = live_stats["by_sentiment"].get(sentiment, 0) + 1

        pct = current / total
        progress_bar.progress(pct)
        status_text.caption(
            f"Ticket {current}/{total} · "
            f"Assigned: {live_stats['assigned']} · "
            f"Unassigned: {live_stats['unassigned']} · "
            f"{city} → {result['assigned_manager']}"
        )

    try:
        router = TicketRouter(ai_mode=ai_mode)
        start_time = time.time()
        results_df = router.route_batch(df, progress_callback=progress_callback)
        elapsed = time.time() - start_time

        progress_bar.progress(1.0)
        status_text.markdown(
            f"**Done.** {total} tickets in {elapsed:.1f}s ({elapsed/total:.1f}s per ticket) · "
            f"Assigned: {live_stats['assigned']} · Unassigned: {live_stats['unassigned']}"
        )

        st.session_state["last_routing_results"] = results_df
        st.session_state["last_routing_stats"] = live_stats

        # Results table
        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
        st.markdown("**Routing Results**")
        display_cols = ["ai_type", "ai_priority", "ai_language", "ai_sentiment",
                        "routed_office", "assigned_manager", "segment", "status"]
        available_cols = [c for c in display_cols if c in results_df.columns]
        st.dataframe(results_df[available_cols], use_container_width=True, hide_index=True)

        # Charts
        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
        _render_inline_charts(live_stats)

    except Exception as e:
        st.error(f"Routing failed: {e}")
        import traceback
        st.code(traceback.format_exc())


def _render_inline_charts(stats: dict):
    """Render charts from batch routing results."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Tickets per City**")
        if stats["by_city"]:
            city_df = pd.DataFrame(
                list(stats["by_city"].items()),
                columns=["City", "Count"]
            ).sort_values("Count", ascending=False)
            st.bar_chart(city_df.set_index("City"))

    with col2:
        st.markdown("**Ticket Types**")
        if stats["by_type"]:
            type_df = pd.DataFrame(
                list(stats["by_type"].items()),
                columns=["Type", "Count"]
            ).sort_values("Count", ascending=False)
            st.bar_chart(type_df.set_index("Type"))

    col3, col4 = st.columns(2)
    with col3:
        st.markdown("**Sentiment**")
        if stats["by_sentiment"]:
            sent_df = pd.DataFrame(
                list(stats["by_sentiment"].items()),
                columns=["Sentiment", "Count"]
            )
            st.bar_chart(sent_df.set_index("Sentiment"))

    with col4:
        st.markdown("**Assignment**")
        assigned = stats.get("assigned", 0)
        unassigned = stats.get("unassigned", 0)
        if assigned + unassigned > 0:
            assign_df = pd.DataFrame({
                "Status": ["Assigned", "Unassigned"],
                "Count": [assigned, unassigned]
            })
            st.bar_chart(assign_df.set_index("Status"))


def _render_statistics():
    """Show persistent statistics from DB."""
    with get_db() as db:
        total_tickets = db.query(Ticket).count()
        assigned_tickets = db.query(Ticket).filter(Ticket.status == "Assigned").count()
        closed_tickets = db.query(Ticket).filter(Ticket.status == "Closed").count()
        new_tickets = db.query(Ticket).filter(Ticket.status == "New").count()

        # Metric cards
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f"""<div class="metric-card">
                <h3>Total Tickets</h3>
                <div class="value">{total_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="metric-card">
                <h3>New</h3>
                <div class="value">{new_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="metric-card">
                <h3>Assigned</h3>
                <div class="value">{assigned_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col4:
            st.markdown(f"""<div class="metric-card">
                <h3>Closed</h3>
                <div class="value">{closed_tickets}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        # Manager load distribution
        st.markdown("**Manager Load Distribution**")
        managers = db.query(Manager).order_by(Manager.current_load.desc()).all()
        if managers:
            manager_df = pd.DataFrame([
                {"Manager": m.name, "Office": m.office_location, "Load": m.current_load, "Role": m.role}
                for m in managers
            ])
            st.bar_chart(manager_df.set_index("Manager")["Load"])

            with st.expander("Full Manager Table"):
                st.dataframe(manager_df, use_container_width=True, hide_index=True)

        # Tickets by office
        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
        st.markdown("**Tickets by Office**")
        tickets = db.query(Ticket).filter(Ticket.assigned_manager_id.isnot(None)).all()
        if tickets:
            office_counts = {}
            for t in tickets:
                mgr = db.query(Manager).filter(Manager.id == t.assigned_manager_id).first()
                if mgr:
                    office = mgr.office_location
                    office_counts[office] = office_counts.get(office, 0) + 1

            if office_counts:
                office_df = pd.DataFrame(
                    list(office_counts.items()),
                    columns=["Office", "Tickets"]
                ).sort_values("Tickets", ascending=False)
                st.bar_chart(office_df.set_index("Office"))
        else:
            st.caption("No assigned tickets yet.")


def _render_management():
    """Admin tools: link managers, view users, reset loads."""
    st.markdown("**Link User → Manager Profile**")
    with get_db() as db:
        users = db.query(User).filter(User.role == "manager").all()
        managers = db.query(Manager).order_by(Manager.name).all()

        if users and managers:
            user_options = {f"{u.username} (ID: {u.id})": u.id for u in users}
            manager_options = {f"{m.name} — {m.office_location}": m.id for m in managers}

            with st.form("link_form"):
                sel_user = st.selectbox("User Account", list(user_options.keys()))
                sel_manager = st.selectbox("Manager Profile", list(manager_options.keys()))
                if st.form_submit_button("Link", type="primary"):
                    uid = user_options[sel_user]
                    mid = manager_options[sel_manager]
                    user = db.query(User).filter(User.id == uid).first()
                    if user:
                        user.manager_id = mid
                        db.flush()
                        st.success(f"Linked {sel_user} → {sel_manager}")
        else:
            st.caption("No manager users or profiles to link.")

    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

    st.markdown("**Reset Manager Loads**")
    if st.button("Reset All Loads to 0"):
        with get_db() as db:
            db.query(Manager).update({Manager.current_load: 0})
            db.flush()
            st.success("All manager loads reset to 0.")
            st.rerun()
