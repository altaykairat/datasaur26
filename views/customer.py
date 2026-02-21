"""Customer Portal — create tickets and view status."""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from database.connection import get_db
from database.models import Ticket, Manager, TicketStatus
from utils.ocr import extract_text_from_uploaded_file, combine_description_with_ocr, is_ocr_available
from engine.router import TicketRouter


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
    
    # If we just submitted successfully, show a clean confirmation screen instead of the form
    if "submitted_ticket" in st.session_state:
        t_data = st.session_state.pop("submitted_ticket")
        st.success("✅ **Ticket Submitted Successfully**")
        st.markdown(f"""
        - **Ticket ID:** #{t_data['id']}
        - **Date:** {t_data['date']}
        - **Status:** {'Assigned to a Manager' if t_data['status'] == 'Assigned' else 'Waiting for Assignment'}
        
        **Your Issue:**
        > {t_data['snippet']}
        """)
        
        st.markdown("<br>", unsafe_allow_html=True)
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Submit Another Ticket", type="primary", use_container_width=True):
                st.rerun()
        with col2:
            st.caption("You can view updates in the 'My Tickets' tab.")
        
        return # Stop rendering the form below

    # Otherwise, render the form
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

            # Save screenshot to disk and run OCR
            if screenshot is not None:
                import os, uuid
                os.makedirs("input/attachments", exist_ok=True)
                ext = screenshot.name.split('.')[-1] if '.' in screenshot.name else "png"
                filepath = f"input/attachments/cust_{uuid.uuid4().hex[:8]}.{ext}"
                with open(filepath, "wb") as f:
                    f.write(screenshot.getbuffer())
                flags["attachment_path"] = filepath

                if is_ocr_available():
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
                with st.spinner("Analyzing and routing your ticket..."):
                    router = TicketRouter(ai_mode="deepseek")  # Or phi4 depending on default
                    
                    with get_db() as db:
                        # Call routing pipeline
                        result = router.route_single_ticket(
                            ticket_description=final_description,
                            segment="Mass", # Default for customer portal
                            client_city=None, # Optionally get from user profile in future
                            client_region=None,
                            ticket_id="",
                            db=db
                        )
                        
                        # Apply OCR flags as well
                        if flags:
                            if result["flags"]:
                                result["flags"].update(flags)
                            else:
                                result["flags"] = flags
                        
                        ticket = Ticket(
                            customer_id=st.session_state["user_id"],
                            description=final_description,
                            status=result["status"],
                            assigned_manager_id=result["assigned_manager_id"],
                            ai_analysis_json=result["ai_analysis"],
                            routed_branch_id=result.get("routed_branch_id"),
                            alternative_branches=result.get("alternative_branches", []),
                            office_rule=result.get("office_rule"),
                            routing_trace=result.get("routing_trace"),
                            flags=result["flags"],
                            correlation_id=result["correlation_id"],
                            workload_at_assignment=result.get("workload_at_assignment"),
                            created_at=datetime.now(timezone.utc),
                        )
                        db.add(ticket)
                        db.commit() # Router flushed but we need to commit the new ticket
                        
                        st.session_state["submitted_ticket"] = {
                            "id": ticket.id,
                            "status": result["status"],
                            "date": ticket.created_at.strftime("%Y-%m-%d %H:%M UTC"),
                            "snippet": final_description[:100] + "..." if len(final_description) > 100 else final_description
                        }
                        st.rerun()


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

            clean_desc = t.description.split('\n\n---')[0]
            rows.append({
                "ID": t.id,
                "Status": t.status,
                "Type": ai_type,
                "Assigned To": manager_name,
                "Created": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "—",
                "Description": clean_desc[:80] + "..." if len(clean_desc) > 80 else clean_desc,
            })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
