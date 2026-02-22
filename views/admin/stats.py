"""Admin Subpackage: Statistics UI

Displays persistent multi-dimensional database metrics and ticket assignment visualizations.
"""
import streamlit as st
import pandas as pd
from database.connection import get_db
from database.models import Ticket, Manager

def render_statistics():
    """Show persistent statistics from DB."""
    st.button("Refresh Data", use_container_width=True)
    
    with get_db() as db:
        total_tickets = db.query(Ticket).count()
        assigned_tickets = db.query(Ticket).filter(Ticket.status == "Assigned").count()
        closed_tickets = db.query(Ticket).filter(Ticket.status == "Closed").count()
        new_tickets = db.query(Ticket).filter(Ticket.status == "New").count()
        failed_tickets = db.query(Ticket).filter(
            Ticket.status.in_(["EnrichFailed", "RoutingFailed", "DeadLetter"])
        ).count()
        spam_tickets = db.query(Ticket).filter(Ticket.status == "Spam").count()
        
        clarification_tickets = db.query(Ticket).filter(
            (Ticket.status == "NeedsClarification") | 
            (Ticket.flags.op('->>')('needs_clarification') == 'true')
        ).count()
        
        review_tickets = db.query(Ticket).filter(
            Ticket.flags.op('->>')('needs_review') == 'true'
        ).count()

        # Metric cards
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.markdown(f"""<div class="metric-card">
                <h3>Total</h3>
                <div class="value">{total_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="metric-card">
                <h3>Assigned</h3>
                <div class="value">{assigned_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="metric-card">
                <h3>Closed</h3>
                <div class="value">{closed_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col4:
            st.markdown(f"""<div class="metric-card">
                <h3>Spam</h3>
                <div class="value">{spam_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col5:
            st.markdown(f"""<div class="metric-card" style="border-left: 4px solid #FFC107;">
                <h3>Clarify 🟡</h3>
                <div class="value">{clarification_tickets}</div>
            </div>""", unsafe_allow_html=True)
        with col6:
            st.markdown(f"""<div class="metric-card" style="border-left: 4px solid #F44336;">
                <h3>Review 🔴</h3>
                <div class="value">{review_tickets}</div>
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
                flag_indicator = ""
                if t.flags and isinstance(t.flags, dict):
                    flags_list = []
                    for k, v in t.flags.items():
                        if v is True:
                            flags_list.append(k.replace('_', ' ').title())
                        elif v is not False and k != "ocr_chars":
                            flags_list.append(f"{k.replace('_', ' ').title()}: {v}")
                    if flags_list:
                        flags_text = ", ".join(flags_list)
                        
                    if t.flags.get("needs_review"):
                        flag_indicator = "Needs Review"
                    elif t.flags.get("needs_clarification"):
                        flag_indicator = "Needs Clarification"

                ticket_rows.append({
                    "ID": t.client_guid,
                    "Flag": flag_indicator if flag_indicator else "—",
                    "Created": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "—",
                    "Status": t.status,
                    "Segment": t.segment or "—",
                    "AI Type": ai_type,
                    "Assigned": mgr_name,
                    "Office": office_name,
                })
            
            # Create DataFrame
            df_tickets = pd.DataFrame(ticket_rows)
            
            # Reorder columns slightly to put Flag near the front
            cols = ["ID", "Flag", "Created", "Status", "Segment", "AI Type", "Assigned", "Office"]
            df_tickets = df_tickets[cols]
            
            # Apply styles based on the 'Flag' column
            def highlight_flags(row):
                if row.get("Flag") == "Needs Review":
                    return ['background-color: rgba(255, 50, 50, 0.2)'] * len(row)
                elif row.get("Flag") == "Needs Clarification":
                    return ['background-color: rgba(255, 200, 0, 0.2)'] * len(row)
                return [''] * len(row)
                
            styled_df = df_tickets.style.apply(highlight_flags, axis=1)
            
            # Use on_select to capture row clicks
            event = st.dataframe(
                styled_df,
                use_container_width=True,
                hide_index=True,
                selection_mode="single-row",
                on_select="rerun"
            )
            
            # Show inspector if a row is selected
            selected_rows = event.selection.rows
            if selected_rows:
                st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
                st.markdown("### Ticket Inspector")
                
                # Get the ID from the selected row index
                row_idx = selected_rows[0]
                # 'ID' is now the client_guid string
                inspect_id = str(df_tickets.iloc[row_idx]["ID"])
                
                inspect_t = db.query(Ticket).filter(Ticket.client_guid == inspect_id).first()
                if inspect_t:
                    st.markdown(f"**Ticket #{inspect_t.client_guid} — {inspect_t.status}**")
                    
                    # Display Client Info if available
                    if inspect_t.flags and ("client_gender" in inspect_t.flags or "client_age" in inspect_t.flags):
                        gender = inspect_t.flags.get("client_gender", "Unknown")
                        age = inspect_t.flags.get("client_age", "Unknown")
                        st.caption(f"**Client Profile:** {gender}, {age} years old")
                        
                    colA, colB = st.columns([1, 1])
                    with colA:
                        st.markdown("**Description:**")
                        # Strip OCR text from description
                        clean_desc = inspect_t.description.split('\n\n---')[0]
                        st.info(clean_desc)
                        
                        st.markdown("**AI Analysis Overview:**")
                        if inspect_t.ai_analysis_json and isinstance(inspect_t.ai_analysis_json, dict):
                            ai = inspect_t.ai_analysis_json
                            lines = []
                            for k, v in ai.items():
                                if k == "summary":
                                    continue # Skip summary to save space or show separately if needed
                                formatted_key = k.replace('_', ' ').title()
                                lines.append(f"- **{formatted_key}:** {v}")
                            
                            st.markdown("\n".join(lines))
                            if "summary" in ai:
                                st.markdown(f"**Brief:** {ai['summary']}")
                        else:
                            st.caption("No AI analysis available.")
                            
                    with colB:
                        st.markdown("**Attachment:**")
                        if inspect_t.flags and inspect_t.flags.get("attachment_path"):
                            path = inspect_t.flags.get("attachment_path")
                            import os
                            if os.path.exists(path):
                                st.image(path, caption="Uploaded Image", use_container_width=True)
                            else:
                                st.warning(f"Attachment file moving/missing.")
                        else:
                            st.caption("No attachment for this ticket.")
        else:
            st.caption("No tickets in the system.")
