"""Manager Portal — view and close assigned tickets."""
import streamlit as st
import pandas as pd

from database.connection import get_db
from database.models import Ticket, Manager, User, TicketStatus


def render_manager():
    """Render the Manager Portal page."""
    st.markdown("## Manager Workspace")
    st.caption("View and resolve your assigned tickets")
    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

    manager_id = st.session_state.get("manager_id")

    if not manager_id:
        st.warning("Your account is not linked to a manager profile yet.")
        _render_link_manager()
        return

    _render_workspace(manager_id)


def _render_link_manager():
    """Allow a manager user to self-link to a manager profile."""
    st.caption("Select your manager profile to view assigned tickets.")

    with get_db() as db:
        managers = db.query(Manager).order_by(Manager.name).all()
        if not managers:
            st.error("No manager profiles found in the system.")
            return

        manager_options = {f"{m.name} — {m.office_location} ({m.role})": m.id for m in managers}

        with st.form("link_manager_form"):
            selected = st.selectbox("Select your profile", list(manager_options.keys()))
            submitted = st.form_submit_button("Link Account", use_container_width=True, type="primary")

            if submitted and selected:
                mid = manager_options[selected]
                user = db.query(User).filter(User.id == st.session_state["user_id"]).first()
                if user:
                    user.manager_id = mid
                    db.flush()
                    st.session_state["manager_id"] = mid
                    st.success("Account linked.")
                    st.rerun()


def _render_workspace(manager_id: int):
    """Show assigned tickets and close actions."""
    with get_db() as db:
        manager = db.query(Manager).filter(Manager.id == manager_id).first()
        if not manager:
            st.error("Manager profile not found.")
            return

        # Manager info cards
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"""<div class="metric-card">
                <h3>Name</h3>
                <div class="value sm">{manager.name}</div>
            </div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="metric-card">
                <h3>Office</h3>
                <div class="value sm">{manager.office_location}</div>
            </div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="metric-card">
                <h3>Current Load</h3>
                <div class="value">{manager.current_load}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        # Tickets
        tickets = (
            db.query(Ticket)
            .filter(Ticket.assigned_manager_id == manager_id)
            .order_by(Ticket.created_at.desc())
            .all()
        )

        open_tickets = [t for t in tickets if t.status != TicketStatus.CLOSED.value]
        closed_tickets = [t for t in tickets if t.status == TicketStatus.CLOSED.value]

        tab_open, tab_closed = st.tabs([
            f"Open ({len(open_tickets)})",
            f"Closed ({len(closed_tickets)})"
        ])

        with tab_open:
            if not open_tickets:
                st.info("No open tickets.")
            else:
                for ticket in open_tickets:
                    _render_ticket_card(ticket, db, manager_id)

        with tab_closed:
            if not closed_tickets:
                st.info("No closed tickets.")
            else:
                rows = []
                for t in closed_tickets:
                    ai_type = "—"
                    if t.ai_analysis_json and isinstance(t.ai_analysis_json, dict):
                        ai_type = t.ai_analysis_json.get("type", "—")
                    rows.append({
                        "ID": t.id,
                        "Type": ai_type,
                        "Description": t.description[:80] + "..." if len(t.description) > 80 else t.description,
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_ticket_card(ticket: Ticket, db, manager_id: int):
    """Render a single ticket as an expandable card."""
    ai_type = "—"
    priority = "—"
    sentiment = "—"
    summary = "—"
    if ticket.ai_analysis_json and isinstance(ticket.ai_analysis_json, dict):
        ai_type = ticket.ai_analysis_json.get("type", "—")
        priority = ticket.ai_analysis_json.get("priority", "—")
        sentiment = ticket.ai_analysis_json.get("sentiment", "—")
        summary = ticket.ai_analysis_json.get("summary", "—")

    priority_label = ""
    if isinstance(priority, int):
        if priority >= 7:
            priority_label = "High"
        elif priority >= 4:
            priority_label = "Medium"
        else:
            priority_label = "Low"

    with st.expander(f"Ticket #{ticket.id} · {ai_type} · Priority: {priority} ({priority_label})", expanded=False):
        age = "—"
        gender = "—"
        if ticket.flags and isinstance(ticket.flags, dict):
            age = ticket.flags.get("client_age", "—")
            gender = ticket.flags.get("client_gender", "—")

        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.caption("Category")
            st.write(ai_type)
        with col2:
            st.caption("Priority")
            st.write(str(priority))
        with col3:
            st.caption("Segment")
            st.write(ticket.segment or "—")
        with col4:
            st.caption("Status")
            st.write(ticket.status)
        with col5:
            st.caption("Sentiment")
            st.write(sentiment)

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        colA, colB = st.columns(2)
        with colA:
            st.caption("Gender")
            st.write(str(gender))
        with colB:
            st.caption("Age")
            st.write(str(age))

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        st.caption("Full Address")
        city_str = ticket.client_city or "Unknown City"
        addr_str = ticket.client_address or "Unknown Street"
        if addr_str == "—" or addr_str.strip() == ",":
            addr_str = "No street address provided"
        st.write(f"{city_str}, {addr_str}")

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        if summary and summary != "—":
            st.caption("AI Summary")
            st.write(summary)
            st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        st.caption("Description")
        
        # Strip OCR text from the end of the description if present
        desc_text = str(ticket.description).split('\n\n---')[0].strip()
        
        if not desc_text or desc_text.lower() == "nan":
            st.info("No comments provided.")
        else:
            st.info(desc_text)

        # Image Attachment
        if ticket.flags and isinstance(ticket.flags, dict) and ticket.flags.get("attachment_path"):
            path = ticket.flags.get("attachment_path")
            import os
            if os.path.exists(path):
                st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
                st.caption("Attached Image")
                st.image(path, use_container_width=True)

        if ticket.status != TicketStatus.CLOSED.value:
            st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
            if st.button(f"Close Ticket #{ticket.id}", key=f"close_{ticket.id}", type="primary"):
                ticket.status = TicketStatus.CLOSED.value
                manager = db.query(Manager).filter(Manager.id == manager_id).first()
                if manager and manager.current_load > 0:
                    manager.current_load -= 1
                db.flush()
                db.commit()
                st.success(f"Ticket #{ticket.id} closed.")
                st.rerun()
