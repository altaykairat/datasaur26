"""Customer Portal — create tickets and view status."""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from database.connection import get_db
from database.models import Ticket, Manager, TicketStatus
from utils.ocr import extract_text_from_uploaded_file, combine_description_with_ocr, is_ocr_available


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

        screenshot = st.file_uploader(
            "Attach a screenshot (optional)",
            type=["png", "jpg", "jpeg", "bmp", "webp"],
            help="If your issue involves an error screen, attach a screenshot. We'll extract text from it automatically."
        )

        submitted = st.form_submit_button("Submit Ticket", use_container_width=True, type="primary")

        if submitted:
            final_description = description.strip()
            flags = {}

            # Run OCR on uploaded screenshot
            if screenshot is not None and is_ocr_available():
                ocr_result = extract_text_from_uploaded_file(screenshot)
                if ocr_result["success"]:
                    final_description = combine_description_with_ocr(final_description, ocr_result["text"])
                    flags["ocr_extracted"] = True
                    flags["ocr_chars"] = len(ocr_result["text"])
                    st.info(f"📸 Extracted {len(ocr_result['text'])} characters from screenshot")
                else:
                    flags["ocr_failed"] = ocr_result.get("error", "unknown")
                    if not final_description:
                        st.warning("Could not read text from screenshot. Please describe your issue in the text box.")

            if not final_description:
                st.error("Please describe your issue or attach a readable screenshot.")
            else:
                with get_db() as db:
                    ticket = Ticket(
                        customer_id=st.session_state["user_id"],
                        description=final_description,
                        status=TicketStatus.NEW.value,
                        flags=flags if flags else None,
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
