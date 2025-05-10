import streamlit as st
import google.generativeai as genai
import json
import hashlib
import os
import time
import pandas as pd
import logging
from logging.handlers import RotatingFileHandler
import shutil
from datetime import datetime
import platform
import re
import pickle
import sqlite3 # Import the sqlite3 library

# --- Configuration ---
CONFIG = {
    "MIN_LEVEL": 1,
    "MAX_LEVEL": 10,
    "DEFAULT_LEVEL": 3,
    "MAX_RETRIES": 3,
    "WORD_RANGES": {2: "50-80", 4: "80-120", 6: "120-180", 8: "180-250", 10: "250-350"},
    "DB_FILE": "user_data.db", # New: SQLite database file
    "LOG_FILE": "app.log",
    "BACKUP_DIR": "backups", # Still keep for DB backups
    "APP_VERSION": "1.3.3", # Updated version after fixes
    "CACHE_FILE": "text_cache.pkl"
}

# Load admin credentials from Streamlit secrets
try:
    ADMIN_USER = st.secrets["ADMIN_USER"]
    ADMIN_PASS = st.secrets["ADMIN_PASS"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError as e:
    st.error(f"Missing secret: {e}. For local testing, set environment variables or create .streamlit/secrets.toml.")
    st.stop() # Stop the app if secrets are missing

# Setup logging with rotation
handler = RotatingFileHandler(CONFIG["LOG_FILE"], maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Security Functions (Moved up to be defined before use) ---
def hash_password(password):
    """Hashes a password using PBKDF2 with a random salt."""
    salt = os.urandom(16)
    # Increased iterations slightly for better future-proofing (optional but good)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 310000)
    return salt.hex() + ':' + pwd_hash.hex()

def verify_password(stored_password_with_salt, provided_password):
    """Verifies a provided password against a stored hash and salt."""
    try:
        salt_hex, stored_hash_hex = stored_password_with_salt.split(':')
        salt = bytes.fromhex(salt_hex)
        # Use the same number of iterations as hashing
        pwd_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 310000)
        return pwd_hash == bytes.fromhex(stored_hash_hex)
    except Exception as e:
        # Log error but return False for security
        logger.error(f"Password verification error: {e}")
        return False


# --- Database Functions ---

def get_db_connection():
    """Creates and returns a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(CONFIG["DB_FILE"])
        conn.row_factory = sqlite3.Row # Allows accessing columns by name
        # Add PRAGMAs for better performance and concurrent read handling (optional but good practice)
        # Note: For true high concurrency or multi-instance deployment, a
        # dedicated database server (PostgreSQL, MySQL) is recommended over SQLite.
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        st.error(f"Error connecting to database: {e}")
        return None

def init_db():
    """Initializes the database: creates the users table and ensures admin exists."""
    conn = get_db_connection()
    if conn is None:
        # If connection fails, stop initialization
        return

    try:
        with conn: # Use 'with' for transaction management
            # Corrected CREATE TABLE statement: removed NOT NULL from current_level
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY UNIQUE,
                    hashed_password_with_salt TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    current_level INTEGER DEFAULT {CONFIG["DEFAULT_LEVEL"]}, -- Removed NOT NULL, allows NULL for admins
                    history TEXT NOT NULL DEFAULT '[]' -- Store history as JSON string
                )
            """) # No second argument needed here anymore

            logger.info("Database table 'users' checked/created.")

            # Ensure admin user exists
            admin_user_data = get_user(ADMIN_USER)
            if admin_user_data is None:
                # hash_password is now defined before this call
                hashed_pass = hash_password(ADMIN_PASS)
                # For the admin user specifically, level is None, history is empty
                # This INSERT is now valid because current_level is nullable
                conn.execute("INSERT INTO users (username, hashed_password_with_salt, is_admin, current_level, history) VALUES (?, ?, ?, ?, ?)",
                            (ADMIN_USER, hashed_pass, 1, None, json.dumps([])))
                logger.info(f"Admin user '{ADMIN_USER}' created.")
            else:
                 # Optional: Re-verify admin password hash on startup and update if secrets changed
                 # Note: This assumes the admin user is the only one who might have secrets-based creds
                 # verify_password is now defined before this call
                 if not verify_password(admin_user_data['hashed_password_with_salt'], ADMIN_PASS):
                     hashed_pass = hash_password(ADMIN_PASS)
                     conn.execute("UPDATE users SET hashed_password_with_salt = ? WHERE username = ?", (hashed_pass, ADMIN_USER))
                     logger.warning(f"Admin password hash updated for '{ADMIN_USER}' due to secrets change.")

    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        st.error(f"Error initializing database: {e}")
        # Consider st.stop() here if DB initialization is critical

def get_user(username):
    """Retrieves a user's data by username."""
    conn = get_db_connection()
    if conn is None:
        return None
    try:
        # No 'with' needed for a single SELECT query that doesn't modify data
        cursor = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
        user_data = cursor.fetchone()
        conn.close() # Close connection after query

        if user_data:
             # Convert Row object to dict and parse history JSON
            user_dict = dict(user_data)
            try:
                user_dict['history'] = json.loads(user_dict['history'])
            except json.JSONDecodeError:
                logger.error(f"Failed to decode history JSON for user '{username}'. Resetting history.")
                user_dict['history'] = [] # Reset corrupted history
                # Optionally, attempt to save the reset history back to the DB here
                # This requires a new connection to avoid using the one being closed
                # reset_conn = get_db_connection()
                # if reset_conn:
                #     try: update_user(username, history=[], conn=reset_conn) # Add conn parameter to update_user
                #     finally: reset_conn.close()


            return user_dict
        return None
    except sqlite3.Error as e:
        logger.error(f"Error getting user '{username}': {e}")
        st.error(f"Error retrieving user data: {e}")
        return None
    finally:
        if conn:
            conn.close()


def add_user(username, password, is_admin=False, level=CONFIG["DEFAULT_LEVEL"]):
    """Adds a new user to the database."""
    conn = get_db_connection()
    if conn is None:
        return False
    try:
        # hash_password is now defined before this call
        hashed_pass = hash_password(password)
        with conn: # Use 'with' for transaction management
            conn.execute("INSERT INTO users (username, hashed_password_with_salt, is_admin, current_level, history) VALUES (?, ?, ?, ?, ?)",
                        (username, hashed_pass, 1 if is_admin else 0, level, json.dumps([])))
        logger.info(f"User '{username}' added to DB.")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"Attempted to add duplicate user '{username}'.")
        return False # User already exists
    except sqlite3.Error as e:
        logger.error(f"Error adding user '{username}': {e}")
        st.error(f"Error adding user: {e}")
        return False
    finally:
        if conn:
            conn.close()


def update_user(username, **kwargs):
    """Updates specified fields for a user."""
    conn = get_db_connection()
    if conn is None:
        return False

    if not kwargs:
        return True # Nothing to update

    set_clauses = []
    values = []
    for key, value in kwargs.items():
        if key == 'history':
            # Ensure history is stored as a JSON string
            values.append(json.dumps(value, ensure_ascii=False))
        else:
             values.append(value)
        set_clauses.append(f"{key} = ?")

    sql = f"UPDATE users SET {', '.join(set_clauses)} WHERE username = ?"
    values.append(username)

    try:
        with conn: # Use 'with' for transaction management
            conn.execute(sql, values)
        logger.info(f"User '{username}' updated with {list(kwargs.keys())}.") # Log keys updated
        return True
    except sqlite3.Error as e:
        logger.error(f"Error updating user '{username}': {e}")
        st.error(f"Error updating user data: {e}")
        return False
    finally:
        if conn:
            conn.close()


def get_all_students():
    """Retrieves data for all non-admin users."""
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        # No 'with' needed for SELECT
        cursor = conn.execute("SELECT username, current_level, history FROM users WHERE is_admin = 0")
        students = []
        for row in cursor.fetchall():
            student = dict(row)
            try:
                history = json.loads(student['history'])
                # Get date from the last history entry if exists
                student['Última Práctica'] = history[-1]['date'].split(' ')[0] if history else 'N/A' # Only show date part
            except (json.JSONDecodeError, KeyError, IndexError): # Catch JSON error, missing key, or empty history list
                 logger.warning(f"Could not get last practice date for student '{student.get('username', 'N/A')}'. History might be empty or corrupted.")
                 student['Última Práctica'] = 'N/A'

            # Remove the full history list from this view unless needed later
            del student['history']
            students.append(student)
        return students
    except sqlite3.Error as e:
        logger.error(f"Error getting all students: {e}")
        st.error(f"Error retrieving student list: {e}")
        return []
    finally:
        if conn:
            conn.close()


# --- Initial Database Setup ---
# This call is now safe as hash_password is defined above
init_db()

# --- Cache Functions (Keep from original) ---
def load_cache():
    """Loads cached text from pickle file."""
    try:
        if os.path.exists(CONFIG["CACHE_FILE"]):
            with open(CONFIG["CACHE_FILE"], 'rb') as f:
                return pickle.load(f)
        return {} # Return empty dict if file doesn't exist
    except Exception as e: # Catch all exceptions during load, including FileNotFoundError
        logger.error(f"Error loading cache: {e}")
        # Consider deleting corrupted cache file here if load fails
        # if os.path.exists(CONFIG["CACHE_FILE"]):
        #     try: os.remove(CONFIG["CACHE_FILE"])
        #     except: pass
        return {} # Return empty dict on error

def save_cache(cache):
    """Saves cache dictionary to pickle file."""
    try:
        with open(CONFIG["CACHE_FILE"], 'wb') as f:
            pickle.dump(cache, f)
    except Exception as e:
        logger.error(f"Error saving cache: {e}")

# --- Gemini Configuration (Keep from original) ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    # Adjusted safety settings slightly for robustness - BLOCK_NONE means rely on model's internal filtering
    # Keeping a low block on Dangerous Content is generally a good idea
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_LOW_AND_ABOVE"} # Still block dangerous content
    ]
     # Use gemini-1.5-pro for potentially better generation quality, fallback if needed
    try:
        model = genai.GenerativeModel('gemini-1.5-pro', safety_settings=safety_settings)
        logger.info("Using gemini-1.5-pro model.")
    except Exception as e_pro:
        logger.warning(f"gemini-1.5-pro not available or failed: {e_pro}. Falling back to gemini-1.5-flash.")
        try:
            model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_settings)
            logger.info("Using gemini-1.5-flash model.")
        except Exception as e_flash:
            logger.error(f"gemini-1.5-flash also failed: {e_flash}.")
            st.error(f"Gemini API model setup failed: {e_flash}. Could not load either 1.5-pro or 1.5-flash.")
            st.stop() # Stop if no suitable model loads

except Exception as e_config:
    logger.error(f"Gemini API configuration failed: {e_config}")
    st.error(f"Gemini API configuration failed: {e_config}")
    st.stop()


# --- Gemini Content Generation (Minor adjustments) ---
def generate_reading_text(level):
    """Generates a reading text based on the user's level, with caching."""
    cache = load_cache()
    cache_key = f"level_{level}_{datetime.now().strftime('%Y%m%d')}"
    if cache_key in cache:
        logger.info(f"Using cached text for {cache_key}")
        return cache[cache_key]

    # Map level to the closest available difficulty key
    level_keys = sorted(CONFIG["WORD_RANGES"].keys())
    # Find the key in level_keys that is closest to the current level
    mapped_level_key = min(level_keys, key=lambda x: abs(x - level))

    difficulty_map = {
        2: ("muy fácil, A1-A2 CEFR", CONFIG["WORD_RANGES"].get(2, "50-80"), "una descripción simple de un animal o mascota"),
        4: ("fácil, A2-B1 CEFR", CONFIG["WORD_RANGES"].get(4, "80-120"), "una anécdota breve de la vida cotidiana"),
        6: ("intermedio, B1 CEFR", CONFIG["WORD_RANGES"].get(6, "120-180"), "un resumen de una noticia sencilla o un evento histórico corto"),
        8: ("intermedio-alto, B2 CEFR", CONFIG["WORD_RANGES"].get(8, "180-250"), "una explicación de un concepto científico básico o un fenómeno natural"),
        10: ("avanzado, C1 CEFR", CONFIG["WORD_RANGES"].get(10, "250-350"), "un análisis corto de un tema social o cultural, o una descripción de un lugar complejo")
    }
    # Use the mapped_level_key to get the appropriate difficulty info
    difficulty_desc, words, topic = difficulty_map.get(mapped_level_key, difficulty_map[max(level_keys)]) # Fallback to highest level if mapping somehow fails

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt = f"""
    Eres un experto en ELE para estudiantes de 16-17 años. Genera un texto en español de nivel {difficulty_desc} (equivalente aproximado a nivel {level}/10),
    con {words} palabras, sobre {topic}. Hazlo interesante, educativo y seguro para menores (G-rated).
    Evita temas sensibles, lenguaje inapropiado o temas que puedan generar controversia o ansiedad. Usa un lenguaje claro y adaptado al nivel.
    Para asegurar variedad, considera que esta solicitud se hace en {timestamp}.
    Devuelve solo el texto, sin títulos ni comentarios adicionales.
    """

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = model.generate_content(prompt)
            # Check if the response is blocked due to safety settings
            if not response.candidates:
                 logger.warning(f"Text generation blocked by safety settings on attempt {attempt+1}. Prompt: {prompt}")
                 if attempt < CONFIG["MAX_RETRIES"] - 1:
                      time.sleep(1.5 ** (attempt + 1))
                      continue # Try next attempt
                 st.error("La generación de texto fue bloqueada por el filtro de seguridad.")
                 return None

            text = response.text.strip()
            # Basic check to ensure generated text is not just whitespace or too short
            # Re-check word count roughly
            word_count = len(text.split())
            try:
                min_words = int(words.split('-')[0])
            except (ValueError, IndexError):
                min_words = 50 # Default safety minimum

            # Allow some deviation from target word count
            if text and word_count >= min_words * 0.8 and len(text) > 100: # Also check raw length
                logger.info(f"Generated text (len={word_count}) for level {level} at {timestamp}")
                cache[cache_key] = text
                save_cache(cache)
                return text
            logger.warning(f"Generated text too short (len={word_count}, min={min_words*0.8}) or empty on attempt {attempt+1}")
        except Exception as e:
            logger.error(f"Text generation attempt {attempt+1} failed: {e}")
            if attempt < CONFIG["MAX_RETRIES"] - 1:
                time.sleep(1.5 ** (attempt + 1)) # Exponential backoff
    st.error("Failed to generate text after retries.")
    return None


def generate_mc_questions(text):
    """Generates multiple-choice questions based on a given text."""
    json_example = '[{"question": "Pregunta de ejemplo", "options": {"A": "Opción A", "B": "Opción B", "C": "Opción C", "D": "Opción D"}, "correct_answer": "A"}]' # More complete example

    prompt = (
        "Basado solo en este texto:\n"
        "---\n"
        f"{text}\n"
        "---\n"
        "Genera exactamente 5 preguntas de opción múltiple en español con 4 opciones (A, B, C, D) por pregunta, una sola correcta. "
        "Cubre idea principal, detalles clave, inferencias obvias y vocabulario del texto. "
        "Usa un lenguaje claro y opciones plausibles pero distintas. "
        f"Devuelve solo una lista JSON válida. Ejemplo de formato: {json_example}. Asegúrate de que sea solo el JSON, sin texto explicativo adicional."
        "The correct answer key in options must match the correct_answer value (e.g., if correct_answer is 'B', options must have key 'B')." # Added clarity for model
    )

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = model.generate_content(prompt)
            # Check if the response is blocked
            if not response.candidates:
                 logger.warning(f"Question generation blocked by safety settings on attempt {attempt+1}. Prompt: {prompt[:200]}...")
                 if attempt < CONFIG["MAX_RETRIES"] - 1:
                      time.sleep(1.5 ** (attempt + 1))
                      continue # Try next attempt
                 st.error("La generación de preguntas fue bloqueada por el filtro de seguridad.")
                 return None

            raw_response = response.text.strip()
            logger.info(f"Raw response from Gemini (questions): {raw_response}")
            # Attempt to clean markdown code blocks from the response
            json_text = re.sub(r'^```json\n|```$', '', raw_response, flags=re.MULTILINE | re.DOTALL).strip()
            questions = json.loads(json_text)
            # More robust validation of the structure
            if isinstance(questions, list) and len(questions) == 5 and all(
                isinstance(q, dict) and
                'question' in q and isinstance(q['question'], str) and q['question'].strip() != "" and
                'options' in q and isinstance(q['options'], dict) and len(q['options']) == 4 and
                'correct_answer' in q and isinstance(q['correct_answer'], str) and q['correct_answer'].strip().upper() in ['A', 'B', 'C', 'D'] and
                q['correct_answer'].strip().upper() in q['options'] and # Check if correct answer key exists in options
                all(isinstance(opt, str) and opt.strip() != "" for opt in q['options'].values()) # Ensure options values are non-empty strings
                for q in questions
            ):
                # Ensure correct answer key is uppercase for consistency
                for q in questions:
                    q['correct_answer'] = q['correct_answer'].strip().upper()
                logger.info("Generated questions successfully and validated structure.")
                return questions
            logger.error(f"Invalid question format or count received from API: {questions}. Raw: {raw_response}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing failed on attempt {attempt+1}: {e}. Raw: {raw_response}")
        except Exception as e:
            logger.error(f"Questions generation attempt {attempt+1} failed: {e}")
        if attempt < CONFIG["MAX_RETRIES"] - 1:
            time.sleep(1.5 ** (attempt + 1)) # Exponential backoff
    st.error("Failed to generate questions after retries. Check logs for details.")
    return None


# --- Sidebar ---
st.sidebar.title("📖 Práctica Lectora Adaptativa")
st.sidebar.markdown("""
**Cómo funciona:**
1. Regístrate/Inicia sesión.
2. Practica con textos y preguntas de tu nivel.
3. Recibe retroalimentación y ajusta tu nivel.
""")

# --- Session State ---
# Initialize default state keys if they don't exist
default_state = {
    'logged_in': False, 'username': None, 'is_admin': False, 'current_level': CONFIG["DEFAULT_LEVEL"],
    'current_text': None, 'current_questions': None, 'user_answers': {}, 'submitted_answers': False,
    'score': 0, 'feedback_given': False
}
for key, value in default_state.items():
    if key not in st.session_state:
        st.session_state[key] = value


# --- Authentication and Main App Logic ---

if not st.session_state.logged_in:
    st.title("Bienvenido/a")
    auth_choice = st.radio("Opción:", ("Iniciar Sesión", "Registrarse"), horizontal=True, key="auth_option")

    if auth_choice == "Iniciar Sesión":
        with st.form("login_form"):
            username_input = st.text_input("Email").lower().strip()
            password_input = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Entrar")
            if submitted:
                user_data = get_user(username_input) # Get user data from DB
                # verify_password is now defined before this call
                if user_data and verify_password(user_data["hashed_password_with_salt"], password_input):
                    st.session_state.logged_in = True
                    st.session_state.username = user_data["username"]
                    st.session_state.is_admin = bool(user_data["is_admin"]) # SQLite stores 0/1, convert to bool
                    # Use the level from the DB if it exists and is not None, otherwise default
                    db_level = user_data.get("current_level")
                    st.session_state.current_level = db_level if db_level is not None and not st.session_state.is_admin else CONFIG["DEFAULT_LEVEL"] # Admins don't have a level
                    st.success(f"¡Bienvenido/a {st.session_state.username}!")
                    logger.info(f"Successful login for {st.session_state.username}. Level: {st.session_state.current_level}")
                    # Clear practice state on successful login to ensure a fresh start
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.user_answers = {}
                    st.session_state.submitted_answers = False
                    st.session_state.score = 0
                    st.session_state.feedback_given = False
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas.")
                    logger.warning(f"Failed login attempt for {username_input}")

    else: # Registrarse
        with st.form("register_form"):
            email_input = st.text_input("Email").lower().strip()
            pwd_input = st.text_input("Contraseña", type="password")
            confirm_input = st.text_input("Confirmar", type="password")
            if st.form_submit_button("Registrar"):
                 # Basic email format validation
                if not re.match(r"[^@]+@[^@]+\.[^@]+", email_input):
                     st.error("Formato de email inválido.")
                elif pwd_input != confirm_input:
                     st.error("Las contraseñas no coinciden.")
                elif len(pwd_input) < 8 or not any(c.isupper() for c in pwd_input) or not any(c.isdigit() for c in pwd_input):
                    st.error("Contraseña debe tener 8+ caracteres, al menos una mayúscula y un número.")
                elif get_user(email_input): # Check if user already exists in DB
                    st.error("Usuario ya registrado.")
                else:
                    # add_user is now defined before this call
                    if add_user(email_input, pwd_input): # Add user to DB
                        st.success("¡Registrado! Ahora puedes iniciar sesión.")
                        logger.info(f"New user registered: {email_input}")
                    else:
                        # Error adding user to DB - likely db connection failure caught in add_user
                        st.error("Error al registrar usuario. Por favor, inténtalo de nuevo.")


else: # User is logged in
    st.sidebar.write(f"Usuario: {st.session_state.username}")
    if st.sidebar.button("Cerrar Sesión"):
        # Save user's current level before logging out if they are a student
        if st.session_state.username and not st.session_state.is_admin:
             # Retrieve current user data to ensure we have the latest history before updating
             user_data_on_logout = get_user(st.session_state.username)
             if user_data_on_logout:
                 # Only update level if it has changed in the session state
                 # Note: History is saved per practice round, so we don't need to re-save history on logout
                 if user_data_on_logout.get('current_level') != st.session_state.current_level:
                    update_user(st.session_state.username, current_level=st.session_state.current_level)
                    logger.info(f"Updated level for {st.session_state.username} to {st.session_state.current_level} on logout")
             else:
                 logger.warning(f"User data not found for {st.session_state.username} during logout save.")

        # Clear all session state variables upon logout
        st.session_state.clear()
        # No need to explicitly set logged_in = False as clear() handles it, but harmless
        st.rerun() # Force rerun to show login screen


    if st.session_state.is_admin:
        st.title("Panel de Administración")
        st.warning("Admins cannot access practice mode.")

        st.subheader("Lista de Estudiantes")
        students = get_all_students() # Get student data from DB
        if students:
             # Convert list of dicts to DataFrame for display
            df_students = pd.DataFrame(students)
            # Rename columns for display
            df_students = df_students.rename(columns={'username': 'Email', 'current_level': 'Nivel'})
            st.dataframe(df_students)
        else:
            st.info("No hay estudiantes.")

        # Optional: Add a button to backup the database file
        st.subheader("Copia de Seguridad")
        if st.button("Crear Copia de Seguridad de la Base de Datos"):
             try:
                os.makedirs(CONFIG["BACKUP_DIR"], exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = os.path.join(CONFIG["BACKUP_DIR"], f"user_data_backup_{timestamp}.db")
                # Ensure the database file exists before attempting to copy
                if os.path.exists(CONFIG["DB_FILE"]):
                    shutil.copy2(CONFIG["DB_FILE"], backup_path) # copy2 preserves metadata
                    st.success(f"Copia de seguridad creada: {backup_path}")
                    logger.info(f"Database backup created: {backup_path}")
                else:
                    st.warning(f"El archivo de base de datos '{CONFIG['DB_FILE']}' no existe aún.")
                    logger.warning(f"Attempted to backup DB file '{CONFIG['DB_FILE']}' but it does not exist.")
             except Exception as e:
                 st.error(f"Error creando copia de seguridad: {e}")
                 logger.error(f"Error creating database backup: {e}")


    else: # User is a student
        st.title("🚀 Práctica Lectora")
        st.info(f"Nivel actual: {st.session_state.current_level}")

        # Check if we need to load/generate new content
        if st.session_state.current_text is None or st.session_state.current_questions is None or not st.session_state.current_questions:
            # Only show the button to start/continue if no text/questions are loaded or if questions load failed previously
            # Adjusted button text logic
            button_text = "Comenzar"
            if st.session_state.score > 0 or st.session_state.feedback_given:
                 button_text = "Siguiente"

            if st.button(button_text, type="primary"):
                # Reset score display and feedback status when requesting new text
                st.session_state.score = 0
                st.session_state.feedback_given = False
                with st.spinner("Preparando un texto interesante…"):
                    text = generate_reading_text(st.session_state.current_level)
                    if text:
                        questions = generate_mc_questions(text)
                        if questions:
                            # Reset session state for a new practice round
                            st.session_state.current_text = text
                            st.session_state.current_questions = questions
                            st.session_state.user_answers = {}
                            st.session_state.submitted_answers = False
                            st.rerun() # Rerun to display the text and questions
                        else:
                             # Clear text if question generation failed, so the button reappears
                            st.session_state.current_text = None
                            st.session_state.current_questions = None
                            st.session_state.user_answers = {} # Also reset user answers state
                            st.error("No se pudieron generar preguntas para el texto. Inténtalo de nuevo.")
                    else:
                         # Clear if text generation failed
                         st.session_state.current_text = None
                         st.session_state.current_questions = None
                         st.session_state.user_answers = {} # Also reset user answers state
                         st.error("No se pudo generar un texto de lectura. Inténtalo de nuevo.")


        else: # Text and questions are loaded
            st.markdown(f"<div style='background-color:#f0f2f6;padding:15px;border-radius:5px;'>{st.session_state.current_text}</div>", unsafe_allow_html=True)

            st.subheader("Preguntas:")
            # Use a unique key for the form based on the current text/questions to prevent key errors on rerun with new content
            form_key = f"qa_form_{hash(st.session_state.current_text + json.dumps(st.session_state.current_questions, sort_keys=True))}" # Use sort_keys for consistent hash

            # Check if form has already been submitted in this session state
            is_submitted = st.session_state.submitted_answers

            # Use a container to hold the form so subsequent elements appear below it correctly
            with st.container():
                with st.form(form_key, clear_on_submit=False): # Use clear_on_submit=False to keep selections visible after submission
                    # Store user answers keyed by question index
                    user_selections = {}
                    for i, q in enumerate(st.session_state.current_questions):
                        options = [f"{k}. {v}" for k, v in q["options"].items()]
                        # Use a unique key for each radio button group specific to this form instance
                        radio_key = f"q_{i}_{form_key}"

                        # Determine default index if answers were previously submitted
                        default_index = None
                        if is_submitted and i in st.session_state.user_answers:
                            try:
                                # Find the index of the previously selected option string (e.g., "A. Option A")
                                prev_answer_letter = st.session_state.user_answers[i]
                                # Ensure prev_answer_letter is one of the valid option keys
                                if prev_answer_letter in q['options']:
                                     prev_answer_string_prefix = f"{prev_answer_letter}."
                                     default_index = next((j for j, opt_str in enumerate(options) if opt_str.startswith(prev_answer_string_prefix)), None)
                                else:
                                     logger.warning(f"Invalid previously selected answer letter '{prev_answer_letter}' for question {i}. Not in options.")

                            except Exception as e:
                                logger.warning(f"Error determining default index for question {i} with answer {st.session_state.user_answers[i]}: {e}")


                        selection = st.radio(
                            f"**{i+1}. {q['question']}**",
                            options,
                            index=default_index, # Set default based on submitted answers
                            key=radio_key, # Ensure unique key per question instance
                            disabled=is_submitted # Disable if already submitted
                        )
                        # Store the selected option's letter (A, B, C, D) if an option is selected
                        if selection:
                             user_selections[i] = selection[0] # Get the letter before the '.'

                    # Update the session state user_answers dictionary with current selections
                    # This happens when the form is interacted with, before submission button logic
                    # It's important to do this before the submit button logic so the state is correct upon submit
                    st.session_state.user_answers = user_selections

                    submit_button = st.form_submit_button("Enviar", disabled=is_submitted)


            # Logic that runs *after* the form has been submitted (i.e., on the rerun triggered by submission)
            if is_submitted:
                # Calculate score based on the stored user_answers and correct answers
                score = 0
                # Iterate through the original questions to check against submitted answers
                for i, q in enumerate(st.session_state.current_questions):
                    # Check if the user provided an answer for this question index
                    # And if that answer matches the correct answer letter
                    if i in st.session_state.user_answers and st.session_state.user_answers[i] == q["correct_answer"]:
                        score += 1

                st.session_state.score = score # Update score in session state

                st.metric("Puntuación", f"{score}/5")

                # Display feedback for each question
                st.subheader("Respuestas:")
                for i, q in enumerate(st.session_state.current_questions):
                    user_answer_letter = st.session_state.user_answers.get(i) # Get the selected letter by index
                    correct_answer_letter = q["correct_answer"]
                    correct_option_text = q['options'].get(correct_answer_letter, "Opción no encontrada")


                    if user_answer_letter is not None: # Check if the user actually made a selection for this question
                        user_option_text = q["options"].get(user_answer_letter, "Opción no encontrada")
                        if user_answer_letter == correct_answer_letter:
                            st.success(f"**{i+1}. Correcto.**")
                        else: # Answered, but wrong
                           st.error(f"**{i+1}. Incorrecto.** Tu respuesta fue '{user_answer_letter}. {user_option_text}'. La respuesta correcta era '{correct_answer_letter}. {correct_option_text}'.")
                    else: # Not answered (shouldn't happen with radio buttons unless they are skipped, but good safety)
                         st.warning(f"**{i+1}. No respondido.** La respuesta correcta era '{correct_answer_letter}. {correct_option_text}'.")


                # Adaptive level adjustment and history saving (only do this once per submission)
                # Use the feedback_given flag
                if not st.session_state.feedback_given:
                    percentage = (score / 5) * 100
                    previous_level = st.session_state.current_level

                    level_changed = False
                    if percentage >= 80 and st.session_state.current_level < CONFIG["MAX_LEVEL"]:
                        st.session_state.current_level += 1
                        st.balloons() # Celebrate level up!
                        st.success(f"¡Excelente! Subes al nivel {st.session_state.current_level}.")
                        level_changed = True
                    elif percentage <= 40 and st.session_state.current_level > CONFIG["MIN_LEVEL"]:
                        st.session_state.current_level -= 1
                        st.warning(f"Necesitas un poco más de práctica. Bajas al nivel {st.session_state.current_level}.")
                        level_changed = True
                    else:
                        st.info(f"Buen intento. Te mantienes en el nivel {st.session_state.current_level}.")


                    # Fetch current user data to append history - essential to avoid overwriting history
                    user_data = get_user(st.session_state.username)
                    if user_data:
                        user_history = user_data.get("history", []) # Get existing history
                        user_history.append({
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), # Add timestamp for more detail
                            "level_before_practice": previous_level, # Log level before this practice
                            "level_after_practice": st.session_state.current_level, # Log level after this practice
                            "score": score,
                            "text_snippet": st.session_state.current_text[:150] + "..." if st.session_state.current_text else "N/A" # Store a snippet of the text
                        })
                        # Update user record in the database
                        update_user(st.session_state.username, current_level=st.session_state.current_level, history=user_history)
                        logger.info(f"Saved practice result for {st.session_state.username}: Level {previous_level} -> {st.session_state.current_level}, Score {score}/5. Level changed: {level_changed}")
                    else:
                         logger.error(f"Could not find user {st.session_state.username} to save practice history.")
                         # st.error("Error saving your progress.") # Might be annoying to show this every time


                    st.session_state.feedback_given = True # Mark feedback as given

                # Button to proceed to the next text
                if st.button("Siguiente Texto"):
                    # Reset all practice-specific session state variables
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.user_answers = {}
                    st.session_state.submitted_answers = False
                    st.session_state.score = 0 # Reset score for next round
                    st.session_state.feedback_given = False
                    st.rerun() # Rerun to show the "Comenzar/Siguiente" button

# Footer
st.caption(f"v{CONFIG['APP_VERSION']} - Desarrollado con Streamlit y Gemini por Moris Polanco | mp@ufm.edu | [morispolanco.vercel.app](https://morispolanco.vercel.app)")
