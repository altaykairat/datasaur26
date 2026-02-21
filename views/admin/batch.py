"""Admin Subpackage: Batch Routing UI

Handles the interface for uploading CSVs and triggering the auto-routing engine.
"""
import streamlit as st
import pandas as pd
import time

from engine.router import TicketRouter

def render_batch_routing():
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
                f"Go to **Settings → Clear All Tickets** first, then re-upload."
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
