"""Admin Dashboard — CSV upload, batch routing, live stats, AI toggle."""
import streamlit as st
import pandas as pd
import time

from database.connection import get_db
from database.models import Manager, Ticket, Office, User, RoundRobinState
from engine.router import TicketRouter


def render_admin():
    """Render the Admin Dashboard page."""
    st.markdown("## Admin Dashboard")
    st.caption("Upload tickets, run auto-routing, view system statistics")
    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

    tab_routing, tab_stats, tab_manage, tab_ai = st.tabs([
        "Batch Routing", "Statistics", "Management", "🌟 AI Analytics"
    ])

    with tab_routing:
        _render_batch_routing()

    with tab_stats:
        _render_statistics()

    with tab_manage:
        _render_management()

    with tab_ai:
        from views.vanna_dashboard import render_ai_dashboard
        render_ai_dashboard()


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
        "spam": 0,
        "duplicates": 0,
        "by_city": {},
        "by_type": {},
        "by_sentiment": {},
    }

    def progress_callback(current, total, result):
        live_stats["processed"] = current

        status = result.get("status", "")
        manager = result.get("assigned_manager", "")

        # Skip duplicates and dead-letter rows from stats
        if status in ("DUPLICATE", "DeadLetter"):
            live_stats["duplicates"] += 1
            pct = current / total
            progress_bar.progress(pct)
            status_text.caption(f"Ticket {current}/{total} · Skipped ({status}) · Duplicates: {live_stats['duplicates']}")
            return

        if status == "Spam":
            live_stats["spam"] += 1
        elif manager not in ("Unassigned", "Spam", "Spam — not assigned", "Error"):
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
            f"Spam: {live_stats['spam']} · "
            f"Unassigned: {live_stats['unassigned']} · "
            f"{city} → {manager}"
        )

    try:
        router = TicketRouter(ai_mode=ai_mode)
        start_time = time.time()
        results_df = router.route_batch(df, progress_callback=progress_callback)
        elapsed = time.time() - start_time

        progress_bar.progress(1.0)

        dupes = live_stats['duplicates']
        if dupes > 0 and dupes == total:
            status_text.markdown(
                f"⚠️ **All {total} tickets were duplicates** (already in DB). "
                f"Go to **Management → Clear All Tickets** first, then re-upload."
            )
        elif dupes > 0:
            status_text.markdown(
                f"**Done.** {total} tickets in {elapsed:.1f}s · "
                f"Assigned: {live_stats['assigned']} · Spam: {live_stats['spam']} · "
                f"Unassigned: {live_stats['unassigned']} · ⚠️ Duplicates skipped: {dupes}"
            )
        else:
            status_text.markdown(
                f"**Done.** {total} tickets in {elapsed:.1f}s ({elapsed/total:.1f}s per ticket) · "
                f"Assigned: {live_stats['assigned']} · Spam: {live_stats['spam']} · Unassigned: {live_stats['unassigned']}"
            )

        st.session_state["last_routing_results"] = results_df
        st.session_state["last_routing_stats"] = live_stats

        # Results table
        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
        st.markdown("**Routing Results**")
        display_cols = ["client_guid", "ai_type", "ai_priority", "ai_language", "ai_sentiment", "ai_confidence",
                        "ai_summary", "routed_office", "office_rule", "assigned_manager", "segment", "status"]
        available_cols = [c for c in display_cols if c in results_df.columns]
        st.dataframe(results_df[available_cols], use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Routing failed: {e}")
        import traceback
        st.code(traceback.format_exc())


def _render_statistics():
    """Show persistent statistics from DB."""
    st.button("🔄 Refresh Data", use_container_width=True)
    
    with get_db() as db:
        total_tickets = db.query(Ticket).count()
        assigned_tickets = db.query(Ticket).filter(Ticket.status == "Assigned").count()
        closed_tickets = db.query(Ticket).filter(Ticket.status == "Closed").count()
        new_tickets = db.query(Ticket).filter(Ticket.status == "New").count()
        failed_tickets = db.query(Ticket).filter(
            Ticket.status.in_(["EnrichFailed", "RoutingFailed", "DeadLetter"])
        ).count()
        spam_tickets = db.query(Ticket).filter(Ticket.status == "Spam").count()

        # Metric cards
        col1, col2, col3, col4, col5, col6 = st.columns(6)
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
        with col5:
            st.markdown(f"""<div class="metric-card">
                <h3>Failed</h3>
                <div class="value">{failed_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col6:
            st.markdown(f"""<div class="metric-card">
                <h3>Spam</h3>
                <div class="value">{spam_tickets}</div>
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

        # Ticket Types & Sentiment
        all_tks = db.query(Ticket).all()
        if all_tks:
            st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
            col1, col2 = st.columns(2)

            type_counts = {}
            sentiment_counts = {}

            for t in all_tks:
                if t.ai_analysis_json and isinstance(t.ai_analysis_json, dict):
                    t_type = t.ai_analysis_json.get("type", "Unknown")
                    t_sent = t.ai_analysis_json.get("sentiment", "Unknown")
                    type_counts[t_type] = type_counts.get(t_type, 0) + 1
                    sentiment_counts[t_sent] = sentiment_counts.get(t_sent, 0) + 1

            with col1:
                st.markdown("**Ticket Types**")
                if type_counts:
                    type_df = pd.DataFrame(
                        list(type_counts.items()),
                        columns=["Type", "Count"]
                    ).sort_values("Count", ascending=False)
                    st.bar_chart(type_df.set_index("Type"))
                else:
                    st.caption("No type data.")

            with col2:
                st.markdown("**Sentiment Analysis**")
                if sentiment_counts:
                    sent_df = pd.DataFrame(
                        list(sentiment_counts.items()),
                        columns=["Sentiment", "Count"]
                    ).sort_values("Count", ascending=False)
                    st.bar_chart(sent_df.set_index("Sentiment"))
                else:
                    st.caption("No sentiment data.")

        # Detailed Tickets Table
        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
        st.markdown("**All Tickets**")
        all_tickets = db.query(Ticket).order_by(Ticket.created_at.desc()).limit(200).all()
        if all_tickets:
            ticket_rows = []
            for t in all_tickets:
                mgr_name = "—"
                office_name = "—"
                if t.assigned_manager_id:
                    mgr = db.query(Manager).filter(Manager.id == t.assigned_manager_id).first()
                    if mgr:
                        mgr_name = mgr.name
                        office_name = mgr.office_location
                
                ai_type = "—"
                if t.ai_analysis_json and isinstance(t.ai_analysis_json, dict):
                    ai_type = t.ai_analysis_json.get("type", "—")
                
                flags_text = "—"
                if t.flags and isinstance(t.flags, dict):
                    flags_list = []
                    for k, v in t.flags.items():
                        if v is True:
                            flags_list.append(k.replace('_', ' ').title())
                        elif v is not False and k != "ocr_chars":
                            flags_list.append(f"{k.replace('_', ' ').title()}: {v}")
                    if flags_list:
                        flags_text = ", ".join(flags_list)

                ticket_rows.append({
                    "ID": t.id,
                    "Created": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "—",
                    "Status": t.status,
                    "Segment": t.segment or "—",
                    "AI Type": ai_type,
                    "Assigned": mgr_name,
                    "Office": office_name,
                    "Summary": (t.ai_analysis_json.get("summary", "—") if isinstance(t.ai_analysis_json, dict) else "—")[:80] + "..." if isinstance(t.ai_analysis_json, dict) and t.ai_analysis_json.get("summary") and len(t.ai_analysis_json.get("summary", "")) > 80 else (t.ai_analysis_json.get("summary", "—") if isinstance(t.ai_analysis_json, dict) else "—"),
                    "Attachment": "📎 Yes" if t.flags and t.flags.get("attachment_path") else "—"
                })
            
            st.dataframe(pd.DataFrame(ticket_rows), use_container_width=True, hide_index=True)
            
            st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
            st.markdown("### 🔍 Ticket Inspector")
            st.caption("Enter a Ticket ID from the table above to view its full details and attachments.")
            inspect_id = st.number_input("Ticket ID to inspect", min_value=0, step=1, value=0)
            if inspect_id > 0:
                inspect_t = db.query(Ticket).filter(Ticket.id == inspect_id).first()
                if inspect_t:
                    st.markdown(f"**Ticket #{inspect_t.id} — {inspect_t.status}**")
                    colA, colB = st.columns([1, 1])
                    with colA:
                        st.markdown("**Description:**")
                        st.info(inspect_t.description)
                        st.markdown("**AI Analysis:**")
                        st.json(inspect_t.ai_analysis_json if inspect_t.ai_analysis_json else {})
                    with colB:
                        st.markdown("**Attachment:**")
                        if inspect_t.flags and inspect_t.flags.get("attachment_path"):
                            path = inspect_t.flags.get("attachment_path")
                            import os
                            if os.path.exists(path):
                                st.image(path, caption=path, use_container_width=True)
                            else:
                                st.warning(f"Attachment file not found at: {path}")
                        else:
                            st.caption("No attachment for this ticket.")
                else:
                    st.error("Ticket ID not found.")
        else:
            st.caption("No tickets in the system.")


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

    st.markdown("**Clear Tickets**")
    st.caption("Remove all processed tickets so you can re-upload the same CSV.")
    if st.button("🗑 Clear All Tickets", type="primary"):
        try:
            with get_db() as db:
                from database.models import RoundRobinState
                count = db.query(Ticket).delete(synchronize_session='fetch')
                db.query(RoundRobinState).delete(synchronize_session='fetch')
                db.query(Manager).update({Manager.current_load: 0})
                db.flush()
            st.success(f"✅ Cleared {count} tickets + reset loads + RR state.")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to clear: {e}")
