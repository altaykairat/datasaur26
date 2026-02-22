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


def _get_theme():
    """Get current theme from session state."""
    if "theme" not in st.session_state:
        st.session_state["theme"] = "light"
    return st.session_state["theme"]


def _apply_theme():
    """Apply CSS based on current theme setting."""
    theme = _get_theme()

    if theme == "dark":
        bg = "#0F1117"
        bg_secondary = "#1E1E2E"
        border = "#2D2D3F"
        text_primary = "#F3F4F6"
        text_secondary = "#9CA3AF"
        sidebar_bg = "#111827"
        sidebar_border = "#1F2937"
        card_bg = "#1E1E2E"
        input_bg = "#1E1E2E"
        banner_bg = "#1E293B"
        component_bg = "#1E1E2E"
    else:
        bg = "#FFFFFF"
        bg_secondary = "#F8F9FA"
        border = "#E5E7EB"
        text_primary = "#111827"
        text_secondary = "#6B7280"
        sidebar_bg = "#F9FAFB"
        sidebar_border = "#E5E7EB"
        card_bg = "#FFFFFF"
        input_bg = "#FFFFFF"
        banner_bg = "#F0F4FF"
        component_bg = "#FFFFFF"

    accent = "#F59E0B"
    accent_hover = "#D97706"

    st.markdown(f"""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

        /* ---- Force Streamlit core backgrounds ---- */
        .stApp,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        .main .block-container {{
            background-color: {bg} !important;
            color: {text_primary} !important;
            font-family: 'Inter', sans-serif;
        }}

        [data-testid="stHeader"] {{
            background-color: {bg} !important;
        }}

        [data-testid="stBottom"] {{
            background-color: {bg} !important;
        }}

        /* ---- Sidebar ---- */
        [data-testid="stSidebar"],
        [data-testid="stSidebar"] > div:first-child {{
            background: {sidebar_bg} !important;
            border-right: 1px solid {sidebar_border};
        }}

        /* ---- All text elements ---- */
        .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
        .stApp p, .stApp span, .stApp label, .stApp div {{
            color: {text_primary};
        }}

        .stApp .stCaption, [data-testid="stCaptionContainer"] {{
            color: {text_secondary} !important;
        }}

        /* ---- Tabs ---- */
        .stTabs [data-baseweb="tab-list"] {{
            background-color: transparent;
        }}

        .stTabs [data-baseweb="tab"] {{
            color: {text_secondary};
        }}

        .stTabs [aria-selected="true"] {{
            color: {accent} !important;
        }}

        /* ---- Inputs & Search Bars ---- */
        .stTextInput input, .stTextArea textarea, .stSelectbox [data-baseweb="select"] {{
            background-color: {component_bg} !important;
            color: {text_primary} !important;
        }}

        /* ---- Expander ---- */
        .streamlit-expanderHeader {{
            background-color: {bg_secondary} !important;
            color: {text_primary} !important;
        }}

        /* ---- Data frames & Tables ---- */
        [data-testid="stDataFrame"], [data-testid="stTable"] {{
            background-color: {component_bg} !important;
        }}
        [data-testid="stDataFrame"] div, [data-testid="stTable"] div, th, td {{
            color: {text_primary} !important;
        }}
        th, td {{
            background-color: {component_bg} !important;
        }}

        /* ---- Diagrams & Charts ---- */
        [data-testid="stArrowVegaLiteChart"], [data-testid="stMarkAndMarkContext"], canvas {{
            background-color: {component_bg} !important;
        }}
        text, .marks text {{
            fill: {text_primary} !important;
        }}
        
        /* ---- Drag and Drop Uploader ---- */
        [data-testid="stFileUploader"] {{
            background-color: {component_bg} !important;
        }}
        [data-testid="stFileUploader"] div, [data-testid="stFileUploader"] span, [data-testid="stFileUploader"] small, [data-testid="stFileUploader"] label {{
            color: {text_primary} !important;
        }}

        /* ---- Custom classes ---- */
        .fire-header {{
            color: {text_primary};
            font-size: 1.8rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin-bottom: 0.2rem;
        }}

        .fire-subtitle {{
            color: {text_secondary};
            font-size: 0.9rem;
            font-weight: 400;
            margin-bottom: 1.5rem;
        }}

        .metric-card {{
            background: {card_bg};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 1.2rem 1.4rem;
            margin: 0.4rem 0;
            transition: border-color 0.2s ease;
        }}

        .metric-card:hover {{
            border-color: {accent};
        }}

        .metric-card h3 {{
            color: {text_secondary} !important;
            margin: 0 0 0.4rem 0;
            font-size: 0.75rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .metric-card .value {{
            color: {text_primary} !important;
            font-size: 1.8rem;
            font-weight: 700;
        }}

        .metric-card .value.sm {{
            font-size: 1.1rem;
        }}

        .fire-divider {{
            height: 1px;
            background: {border};
            margin: 1.2rem 0;
            border: none;
        }}

        /* ---- Buttons ---- */
        .stButton > button, button[data-testid="baseButton-secondary"], button {{
            background-color: {component_bg} !important;
            color: {text_primary} !important;
            border-radius: 8px;
            font-weight: 500;
            letter-spacing: 0.01em;
            transition: all 0.2s ease;
        }}

        .stButton > button:hover, button[data-testid="baseButton-secondary"]:hover, button:hover {{
            transform: translateY(-1px);
        }}

        .stProgress > div > div > div > div {{
            background: linear-gradient(90deg, {accent}, {accent_hover});
        }}

        .info-banner {{
            background: {banner_bg};
            border-left: 3px solid {accent};
            border-radius: 0 8px 8px 0;
            padding: 0.8rem 1.2rem;
            margin: 0.5rem 0;
            color: {text_secondary};
            font-size: 0.88rem;
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


# Apply theme on every run
_apply_theme()


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
            from views.admin import render_admin
            render_admin()
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

        role_emoji = {"admin": "👑", "manager": "💼", "customer": "🙋"}
        st.markdown(f"**{role_emoji.get(st.session_state['role'], '❓')} {st.session_state['username']}**")
        st.caption(f"Role: {st.session_state['role'].title()}")

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        if st.button("Logout", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

        st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

        # Theme selector (at bottom of sidebar)
        current_theme = _get_theme()
        theme_options = ["light", "dark"]
        theme_idx = theme_options.index(current_theme)
        new_theme = st.selectbox(
            "Theme",
            theme_options,
            index=theme_idx,
            format_func=lambda t: "☀️ Light" if t == "light" else "🌙 Dark",
            key="theme_selector",
        )
        if new_theme != current_theme:
            st.session_state["theme"] = new_theme
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
