"""Admin Subpackage: Settings UI

Provides tools for managing database state, such as clearing tickets or deleting spam.
"""
import streamlit as st
from database.connection import get_db
from database.models import Manager, Ticket, RoundRobinState


def render_settings():
    """Admin settings: theme selection and database management."""
    st.markdown("### Settings")
    st.caption("Manage system configuration and database state.")
    
    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)
    st.markdown("**Database Management**")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Clear All Tickets**")
        st.caption("Remove all processed tickets so you can re-upload the same CSV.")
        if st.button("Clear All Tickets", type="primary", use_container_width=True):
            try:
                with get_db() as db:
                    count = db.query(Ticket).delete(synchronize_session='fetch')
                    db.query(RoundRobinState).delete(synchronize_session='fetch')
                    db.query(Manager).update({Manager.current_load: 0})
                    db.flush()
                st.success(f"Cleared {count} tickets + reset loads + RR state.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to clear: {e}")

    with col2:
        st.markdown("**Delete Spam Tickets**")
        st.caption("Permanently remove all tickets that were classified as Spam.")
        if st.button("Delete Spam Tickets", type="primary", use_container_width=True):
            try:
                with get_db() as db:
                    count = db.query(Ticket).filter(Ticket.status == "Spam").delete(synchronize_session='fetch')
                    db.flush()
                st.success(f"Deleted {count} Spam tickets.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to delete spam: {e}")
