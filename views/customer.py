"""Customer Portal — create tickets and view status."""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from database.connection import get_db
from database.models import Ticket, Manager, TicketStatus


def render_customer():
    """Render the Customer Portal page."""
    st.markdown("## Customer Portal")
    st.caption("Submit support requests and track their status")
    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

    tab_create, tab_tickets = st.tabs(["New Ticket", "My Tickets"])

    with tab_create:
        _render_create_ticket()

    with tab_tickets:
        _render_my_tickets()


def _render_create_ticket():
    """Form for creating a new support ticket."""
    with st.form("create_ticket_form"):
        description = st.text_area(
            "Describe your issue",
            height=180,
            placeholder="Please describe your issue in detail. Include any relevant account numbers, dates, or error messages..."
        )
        submitted = st.form_submit_button("Submit Ticket", use_container_width=True, type="primary")

        if submitted:
            if not description.strip():
                st.error("Please describe your issue before submitting.")
            else:
                with get_db() as db:
                    ticket = Ticket(
                        customer_id=st.session_state["user_id"],
                        description=description.strip(),
                        status=TicketStatus.NEW.value,
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(ticket)
                    db.flush()
                    st.success(f"Ticket #{ticket.id} created successfully.")


def _render_my_tickets():
    """Display table of the customer's tickets."""
    with get_db() as db:
        tickets = (
            db.query(Ticket)
            .filter(Ticket.customer_id == st.session_state["user_id"])
            .order_by(Ticket.created_at.desc())
            .all()
        )

        if not tickets:
            st.info("No tickets yet. Create one from the 'New Ticket' tab.")
            return

        rows = []
        for t in tickets:
            manager_name = "—"
            if t.assigned_manager_id:
                manager = db.query(Manager).filter(Manager.id == t.assigned_manager_id).first()
                if manager:
                    manager_name = manager.name

            ai_type = "—"
            if t.ai_analysis_json and isinstance(t.ai_analysis_json, dict):
                ai_type = t.ai_analysis_json.get("type", "—")

            rows.append({
                "ID": t.id,
                "Status": t.status,
                "Type": ai_type,
                "Assigned To": manager_name,
                "Created": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "—",
                "Description": t.description[:80] + "..." if len(t.description) > 80 else t.description,
            })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
