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
            "model": "deepseek-coder", 
            "system_prompt": "You are a read-only SQL expert for an AI routing system called FIRE. "
                             "You MUST ONLY output 'SELECT' statements. Never output INSERT, UPDATE, DELETE, DROP, CREATE, or ALTER. "
                             "The user will ask questions about the tickets, managers, and offices tables in Russian. "
                             "Return only valid PostgreSQL SQL queries. Ensure queries are robust and handle edge cases.",
        })

        # Parse DATABASE_URL for Vanna connection
        # Format: postgresql://postgres:admin@localhost:5432/fire_db
        try:
            # We use a simple driver check. Vanna's connect_to_postgres expects standard kwargs
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
        # We define the DDL explicitly to avoid Vanna asking the DB for everything
        # and to focus it purely on analytical queries for the Streamlit dashboard

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
    
    # Strip comments and upper case
    clean_sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    clean_sql = re.sub(r'/\*.*?\*/', '', clean_sql, flags=re.DOTALL)
    clean_sql = clean_sql.upper().strip()

    # It MUST be a select statement (or WITH ... SELECT)
    if not (clean_sql.startswith("SELECT") or clean_sql.startswith("WITH")):
        return False
    
    # Blocklist
    blocked_keywords = [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", 
        "GRANT", "REVOKE", "COMMIT", "ROLLBACK", "EXEC", "EXECUTE"
    ]
    
    for kw in blocked_keywords:
        # Regex matches whole word with boundaries
        if re.search(rf'\b{kw}\b', clean_sql):
            return False
            
    return True

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

    # Display previous messages
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

    # Input box
    prompt = st.chat_input("Например: Покажи количество тикетов по каждому статусу (e.g. show tickets by status)")
    
    if prompt:
        st.session_state["vanna_chat_history"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing request and generating SQL..."):
                try:
                    sql = vn.generate_sql(question=prompt)
                    
                    with st.expander("Generated SQL", expanded=True):
                        st.code(sql, language="sql")
                        
                    if not is_safe_query(sql):
                        st.error("⚠️ Query rejected: Blocked keyword detected or not a SELECT statement.")
                        st.session_state["vanna_chat_history"].append({"role": "assistant", "sql": sql, "error": "Query rejected by security filter."})
                        return

                    # Run SQL
                    df = vn.run_sql(sql=sql)
                    st.dataframe(df, use_container_width=True)
                    
                    # Generate Chart
                    fig = None
                    if not df.empty and len(df.columns) > 1:
                        try:
                            # Try to let Vanna generate plotly code
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
                        "df": df,
                        "fig": fig
                    })

                except Exception as e:
                    st.error(f"Error processing your request: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.session_state["vanna_chat_history"].append({"role": "assistant", "error": str(e)})
