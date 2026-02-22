"""FIRE — Freedom Intelligent Routing Engine.

Main Streamlit application entry point.
"""
import streamlit as st
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.connection import get_db
from database.models import Base, User
from utils.auth import verify_password

# ----- Page Config -----
st.set_page_config(
    page_title="F.I.R.E. — Freedom Intelligent Routing Engine",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _inject_css():
    """Inject custom CSS to enhance layout while respecting Streamlit's native Light/Dark themes."""
    st.markdown(f"""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

        /* ---- Apply font family ---- */
        html, body, [class*="css"] {{
            font-family: 'Inter', sans-serif;
        }}

        /* ---- Custom classes for FIRE layout ---- */
        .fire-header {{
            font-size: 1.8rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin-bottom: 0.2rem;
            color: var(--text-color);
        }}

        .fire-subtitle {{
            font-size: 0.9rem;
            font-weight: 400;
            margin-bottom: 1.5rem;
            color: var(--text-color);
            opacity: 0.7;
        }}

        .metric-card {{
            background: var(--secondary-background-color);
            border: 1px solid rgba(128, 128, 128, 0.2);
            border-radius: 10px;
            padding: 1.2rem 1.4rem;
            margin: 0.4rem 0;
            transition: border-color 0.2s ease;
        }}

        .metric-card:hover {{
            border-color: var(--primary-color);
        }}

        .metric-card h3 {{
            margin: 0 0 0.4rem 0;
            font-size: 0.75rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-color);
            opacity: 0.8;
        }}

        .metric-card .value {{
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--text-color);
        }}

        .metric-card .value.sm {{
            font-size: 1.1rem;
        }}

        .fire-divider {{
            height: 1px;
            background: rgba(128, 128, 128, 0.2);
            margin: 1.2rem 0;
            border: none;
        }}

        .stButton > button {{
            border-radius: 8px;
            font-weight: 500;
            letter-spacing: 0.01em;
            transition: all 0.2s ease;
        }}

        .stButton > button:hover {{
            transform: translateY(-1px);
        }}

        .info-banner {{
            background: var(--secondary-background-color);
            border-left: 3px solid var(--primary-color);
            border-radius: 0 8px 8px 0;
            padding: 0.8rem 1.2rem;
            margin: 0.5rem 0;
            color: var(--text-color);
            font-size: 0.88rem;
            opacity: 0.9;
        }}

        .spam-badge {{
            display: inline-block;
            background: #EF4444;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
    </style>
    """, unsafe_allow_html=True)


# Apply custom CSS using native theme variables on every run
_inject_css()


def init_session_state():
    """Initialize session state with defaults."""
    defaults = {
        "logged_in": False,
        "user_id": None,
        "username": None,
        "role": None,
        "manager_id": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def main():
    init_session_state()

    if not st.session_state["logged_in"]:
        show_login_page()
    else:
        show_sidebar()
        role = st.session_state["role"]
        if role == "admin":
            from views.admin import render_admin_portal
            render_admin_portal()
        elif role == "manager":
            from views.manager import render_manager
            render_manager()
        elif role == "customer":
            from views.customer import render_customer
            render_customer()


def show_sidebar():
    """Render the sidebar with user info, theme toggle, and logout."""
    with st.sidebar:
        st.markdown('#### 🔥 F.I.R.E.', unsafe_allow_html=True)
        st.caption("Freedom Intelligent Routing Engine")
        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        st.markdown(f"**{st.session_state['username']}**")
        st.caption(f"Role: {st.session_state['role'].title()}")

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        if st.button("Logout", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


def show_login_page():
    """Render the login/register page."""
    col1, col2, col3 = st.columns([1, 1.5, 1])

    with col2:
        st.markdown("")
        st.markdown("")
        st.markdown("#### 🔥 F.I.R.E.")
        st.caption("Freedom Intelligent Routing Engine · Customer Support Dispatch")

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        tab_login, tab_register = st.tabs(["Login", "Register"])

        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username", placeholder="Enter your username")
                password = st.text_input("Password", type="password", placeholder="Enter your password")
                submitted = st.form_submit_button("Login", use_container_width=True, type="primary")

                if submitted:
                    if not username or not password:
                        st.error("Please fill in all fields.")
                    else:
                        with get_db() as db:
                            user = db.query(User).filter(User.username == username).first()
                            if user and verify_password(password, user.password_hash):
                                st.session_state["logged_in"] = True
                                st.session_state["user_id"] = user.id
                                st.session_state["username"] = user.username
                                st.session_state["role"] = user.role
                                st.session_state["manager_id"] = user.manager_id
                                st.rerun()
                            else:
                                st.error("Invalid username or password.")

        with tab_register:
            with st.form("register_form"):
                new_username = st.text_input("Username", placeholder="Choose a username", key="reg_user")
                new_password = st.text_input("Password", type="password", placeholder="Choose a password", key="reg_pass")
                role = st.selectbox("Role", ["customer", "manager", "admin"])

                if role == "manager":
                    st.info("After registration, link your account to a manager profile in your workspace.")

                submitted = st.form_submit_button("Register", use_container_width=True, type="primary")

                if submitted:
                    if not new_username or not new_password:
                        st.error("Please fill in all fields.")
                    else:
                        from utils.auth import hash_password
                        with get_db() as db:
                            existing = db.query(User).filter(User.username == new_username).first()
                            if existing:
                                st.error("Username already taken.")
                            else:
                                new_user = User(
                                    username=new_username,
                                    password_hash=hash_password(new_password),
                                    role=role,
                                )
                                db.add(new_user)
                                db.flush()
                                st.success("Account created. Please login.")


if __name__ == "__main__":
    main()
