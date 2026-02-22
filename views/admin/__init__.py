"""Admin View — Orchestrator

The main entry point for the Admin dashboard. It defines the layout (tabs)
and delegates the rendering of each tab to dedicated submodules.
"""
import streamlit as st

# Import modular UI components
from views.admin.batch import render_batch_routing
from views.admin.stats import render_statistics
from views.admin.settings import render_settings
from views.admin.vanna import render_ai_dashboard

def render_admin_portal():
    """Main rendering loop for the Admin Portal."""

    st.markdown('<div class="fire-header">F.I.R.E. Control Center</div>', unsafe_allow_html=True)
    st.markdown('<div class="fire-subtitle">Batch Intelligence Routing & Analytics</div>', unsafe_allow_html=True)

    # Initialize session state for batch results if needed
    if "last_routing_results" not in st.session_state:
        st.session_state["last_routing_results"] = None
    if "last_routing_stats" not in st.session_state:
        st.session_state["last_routing_stats"] = None

    # TABS structure
    tab1, tab2, tab3, tab4 = st.tabs(["⚡ Batch Auto-Routing", "📊 Statistics", "🤖 AI Assistant", "⚙️ Settings"])

    with tab1:
        render_batch_routing()

    with tab2:
        render_statistics()

    with tab3:
        render_ai_dashboard()

    with tab4:
        render_settings()
