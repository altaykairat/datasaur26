"""Vanna AI Dashboard for querying the database with natural language."""
import streamlit as st
import os
import re
from config import DATABASE_URL, DEEPSEEK_API_KEY
from vanna.chromadb import ChromaDB_VectorStore
from vanna.openai import OpenAI_Chat

# Create a custom Vanna class that uses ChromaDB for vector storage and OpenAI compatible API for DeepSeek
class MyVanna(ChromaDB_VectorStore, OpenAI_Chat):
    def __init__(self, config=None):
        if config is None:
            config = {}
        
        # Give ChromaDB ONLY the path
        chroma_config = {"path": config.get("path")} if "path" in config else {}
        ChromaDB_VectorStore.__init__(self, config=chroma_config)
        
        # Give OpenAI the client and model
        openai_config = {
            "client": config.get("client"),
            "model": config.get("model")
        }
        OpenAI_Chat.__init__(self, config=openai_config)
        self.client = config.get("client")

def _init_vanna():
    """Initialize Vanna instance with DeepSeek config and train it on our schema."""
    if "vn" not in st.session_state:
        if not DEEPSEEK_API_KEY:
            st.error("DEEPSEEK_API_KEY is not set in .env")
            return None

        import openai
        client = openai.Client(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com"
        )

        vn = MyVanna(config={
            "client": client,
            "path": "/tmp/vanna_chroma_db",
            "model": "deepseek-chat", # Changed from deepseek-coder to deepseek-chat (standard API)
            "system_prompt": "You are a read-only SQL expert for an AI routing system called FIRE. "
                             "You MUST ONLY output 'SELECT' statements. Never output INSERT, UPDATE, DELETE, DROP, CREATE, or ALTER. "
                             "The user will ask questions about the tickets, managers, and offices tables in Russian. "
                             "Return only valid PostgreSQL SQL queries. Ensure queries are robust and handle edge cases. "
                             "If the user asks a greeting or a question entirely unrelated to the database (tickets, managers, offices), do NOT attempt to query tables. Instead, return this exact safe query: SELECT 'Я ИИ-ассистент системы FIRE. Пожалуйста, задайте вопрос по базе данных (тикеты, менеджеры, офисы).' as response; "
                             "To prevent UI freezing, ALWAYS append LIMIT 300 to your queries unless the user explicitly asks for a specific limit or uses aggregate functions (COUNT, SUM) that return single rows.",
        })

        # Parse DATABASE_URL for Vanna connection
        # Format: postgresql://postgres:admin@localhost:5432/fire_db
        try:
            from sqlalchemy.engine.url import make_url
            url = make_url(DATABASE_URL)
            vn.connect_to_postgres(
                host=url.host,
                dbname=url.database,
                user=url.username,
                password=url.password,
                port=url.port or 5432
            )
            st.session_state["vanna_db_connected"] = True
        except Exception as e:
            st.error(f"Failed to connect Vanna to database: {e}")
            return None

        # --- Train Vanna with our Schema (DDL) ---
        ddl_statements = [
            """
            CREATE TABLE managers (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                role VARCHAR(100) NOT NULL,
                skills JSON NOT NULL,
                office_location VARCHAR(100),
                office_id INTEGER REFERENCES offices(id),
                current_load INTEGER NOT NULL,
                is_active BOOLEAN NOT NULL
            );
            """,
            """
            CREATE TABLE offices (
                id SERIAL PRIMARY KEY,
                city VARCHAR(100) NOT NULL,
                name VARCHAR(150),
                address TEXT,
                lat FLOAT,
                lon FLOAT
            );
            """,
            """
            CREATE TABLE tickets (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER REFERENCES users(id),
                client_guid VARCHAR(100),
                correlation_id VARCHAR(50),
                description TEXT NOT NULL,
                status VARCHAR(20) NOT NULL,
                assigned_manager_id INTEGER REFERENCES managers(id),
                ai_analysis_json JSON,
                segment VARCHAR(50),
                client_city VARCHAR(100),
                client_address TEXT,
                office_rule VARCHAR(100),
                routed_branch_id INTEGER REFERENCES offices(id),
                alternative_branches JSON,
                routing_trace JSON,
                flags JSON,
                workload_at_assignment INTEGER,
                created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
            );
            """
        ]

        for ddl in ddl_statements:
            vn.train(ddl=ddl)
            
        # Add a few example translations to help the LLM map Russian to SQL
        vn.train(documentation="The AI types are defined inside ai_analysis_json->>'type'. "
                               "The sentiments are inside ai_analysis_json->>'sentiment'. "
                               "The column office_location in managers refers to the city. "
                               "The status in tickets includes 'Assigned', 'Closed', 'New', 'Spam'. "
                               "Use the PostgreSQL JSON operator ->> to extract values from ai_analysis_json.")

        # Example Q&A 
        vn.train(question="Покажи распределение типов обращений по городам",
                 sql="SELECT routed_branch_id, o.city as routed_city, "
                     "t.ai_analysis_json->>'type' as ticket_type, COUNT(*) as ticket_count "
                     "FROM tickets t JOIN offices o ON t.routed_branch_id = o.id "
                     "GROUP BY routed_branch_id, o.city, t.ai_analysis_json->>'type' "
                     "ORDER BY ticket_count DESC;"
                 )

        st.session_state["vn"] = vn

    return st.session_state["vn"]

def is_safe_query(sql: str) -> bool:
    """Basic security filter to prevent execution of non-SELECT statements."""
    if not sql:
        return False
    
    clean_sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    clean_sql = re.sub(r'/\*.*?\*/', '', clean_sql, flags=re.DOTALL)
    clean_sql = clean_sql.upper().strip()

    if not (clean_sql.startswith("SELECT") or clean_sql.startswith("WITH")):
        return False
    
    blocked_keywords = [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", 
        "GRANT", "REVOKE", "COMMIT", "ROLLBACK", "EXEC", "EXECUTE"
    ]
    
    for kw in blocked_keywords:
        if re.search(rf'\b{kw}\b', clean_sql):
            return False
            
    return True

@st.cache_data(ttl=3600, show_spinner=False)
def cached_generate_sql(prompt: str) -> str:
    """Cache the LLM generation of SQL to speed up repeated queries."""
    if "vn" not in st.session_state:
        _init_vanna()
    return st.session_state["vn"].generate_sql(question=prompt)

@st.cache_data(ttl=3600, show_spinner=False)
def cached_run_sql(sql: str):
    """Cache the execution of SQL against the Postgres database."""
    if "vn" not in st.session_state:
        _init_vanna()
    return st.session_state["vn"].run_sql(sql=sql)


def render_ai_dashboard():
    """Render the Vanna AI analytics dashboard."""
    st.markdown("## 🌟 Star Task: AI Analytics Companion")
    st.caption("Ask natural language questions about your ticket system in Russian to instantly generate SQL, dataframes, and Plotly charts.")
    st.markdown('<div class="fire-divider"></div>', unsafe_allow_html=True)

    with st.spinner("Initializing DeepSeek and Vector DB..."):
        vn = _init_vanna()

    if not vn:
        st.warning("AI Dashboard could not be initialized.")
        return

    # Chat interface
    if "vanna_chat_history" not in st.session_state:
        st.session_state["vanna_chat_history"] = []

    # 1. The Top-Fixed Input (Search Bar Style)
    with st.form(key="ai_query_form", clear_on_submit=True):
        col1, col2 = st.columns([5, 1])
        with col1:
            prompt = st.text_input("Напишите ваш запрос...", label_visibility="collapsed", placeholder="Например: Покажи распределение типов обращений по городам")
        with col2:
            submitted = st.form_submit_button("Отправить")

    # 2. The Chat History Container (Below the Input)
    chat_container = st.container(height=600, border=True)

    # 3. The Logic (Inside the Container)
    with chat_container:
        # A. Render all past history
        for msg in st.session_state["vanna_chat_history"]:
            with st.chat_message(msg["role"]):
                if msg["role"] == "user":
                    st.write(msg["content"])
                else:
                    if "sql" in msg:
                        with st.expander("Generated SQL", expanded=False):
                            st.code(msg["sql"], language="sql")
                    if "df" in msg and msg["df"] is not None:
                        st.dataframe(msg["df"], use_container_width=True)
                    if "fig" in msg and msg["fig"] is not None:
                        st.plotly_chart(msg["fig"], use_container_width=True)
                    if "error" in msg:
                        st.error(msg["error"])

        # B. Process and render the NEW message inside the container!
        if submitted and prompt:
            st.session_state["vanna_chat_history"].append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.write(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Analyzing request and generating SQL..."):
                    try:
                        sql = cached_generate_sql(prompt)
                        
                        with st.expander("Generated SQL", expanded=True):
                            st.code(sql, language="sql")
                            
                        if not is_safe_query(sql):
                            st.error("⚠️ Query rejected: Blocked keyword detected or not a SELECT statement.")
                            st.session_state["vanna_chat_history"].append({"role": "assistant", "sql": sql, "error": "Query rejected by security filter."})
                            st.rerun()

                        # Run SQL
                        df = cached_run_sql(sql)
                        
                        fig = None
                        if df is None or df.empty:
                            st.info("Нет данных по вашему запросу.")
                        else:
                            st.dataframe(df, use_container_width=True)
                            
                            # Generate Chart only if we have data
                            if len(df.columns) > 1:
                                try:
                                    plotly_code = vn.generate_plotly_code(question=prompt, sql=sql, df=df)
                                    fig = vn.get_plotly_figure(plotly_code=plotly_code, df=df)
                                    if fig:
                                        st.plotly_chart(fig, use_container_width=True)
                                except Exception as e:
                                    st.caption(f"(Could not automatically generate chart: {e})")
                        
                        # Save to history
                        st.session_state["vanna_chat_history"].append({
                            "role": "assistant", 
                            "sql": sql,
                            "df": df if df is not None and not df.empty else None,
                            "fig": fig
                        })

                    except Exception as e:
                        st.error(f"Error processing your request: {e}")
                        import traceback
                        st.code(traceback.format_exc())
                        st.session_state["vanna_chat_history"].append({"role": "assistant", "error": str(e)})
