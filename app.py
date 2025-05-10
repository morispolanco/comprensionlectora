import streamlit as st
import google.generativeai as genai
import json
import hashlib
import os
import time
import pandas as pd
import logging
from logging.handlers import RotatingFileHandler
import shutil # Still useful for backing up the SQLite file itself, if desired
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
    "APP_VERSION": "1.3.0", # Updated version
    "CACHE_FILE": "text_cache.pkl"
}

# Load admin credentials from Streamlit secrets
try:
    ADMIN_USER = st.secrets["ADMIN_USER"]
    ADMIN_PASS = st.secrets["ADMIN_PASS"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except KeyError as e:
    st.error(f"Missing secret: {e}. For local testing, set environment variables or create .streamlit/secrets.toml.")
    st.stop()

# Setup logging with rotation
handler = RotatingFileHandler(CONFIG["LOG_FILE"], maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Database Functions ---

def get_db_connection():
    """Creates and returns a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(CONFIG["DB_FILE"])
        conn.row_factory = sqlite3.Row # Allows accessing columns by name
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        st.error(f"Error connecting to database: {e}")
        return None

def init_db():
    """Initializes the database: creates the users table and ensures admin exists."""
    conn = get_db_connection()
    if conn is None:
        return

    try:
        with conn: # Use 'with' for transaction management
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY UNIQUE,
                    hashed_password_with_salt TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    current_level INTEGER NOT NULL DEFAULT ?,
                    history TEXT NOT NULL DEFAULT '[]' -- Store history as JSON string
                )
            """, (CONFIG["DEFAULT_LEVEL"],))
            logger.info("Database table 'users' checked/created.")

            # Ensure admin user exists
            admin_user_data = get_user(ADMIN_USER)
            if admin_user_data is None:
                hashed_pass = hash_password(ADMIN_PASS)
                conn.execute("INSERT INTO users (username, hashed_password_with_salt, is_admin, current_level, history) VALUES (?, ?, ?, ?, ?)",
                            (ADMIN_USER, hashed_pass, 1, None, json.dumps([]))) # Admin level can be None, history is empty list
                logger.info(f"Admin user '{ADMIN_USER}' created.")
            else:
                 # Optional: Re-verify admin password hash on startup and update if secrets changed
                 if not verify_password(admin_user_data['hashed_password_with_salt'], ADMIN_PASS):
                     hashed_pass = hash_password(ADMIN_PASS)
                     conn.execute("UPDATE users SET hashed_password_with_salt = ? WHERE username = ?", (hashed_pass, ADMIN_USER))
                     logger.warning(f"Admin password hash updated for '{ADMIN_USER}' due to secrets change.")

    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        st.error(f"Error initializing database: {e}")

def get_user(username):
    """Retrieves a user's data by username."""
    conn = get_db_connection()
    if conn is None:
        return None
    try:
        with conn:
            cursor = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
            user_data = cursor.fetchone()
            if user_data:
                 # Convert Row object to dict and parse history JSON
                user_dict = dict(user_data)
                user_dict['history'] = json.loads(user_dict['history'])
                return user_dict
            return None
    except sqlite3.Error as e:
        logger.error(f"Error getting user '{username}': {e}")
        st.error(f"Error retrieving user data: {e}")
        return None

def add_user(username, password, is_admin=False, level=CONFIG["DEFAULT_LEVEL"]):
    """Adds a new user to the database."""
    conn = get_db_connection()
    if conn is None:
        return False
    try:
        hashed_pass = hash_password(password)
        with conn:
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
        with conn:
            conn.execute(sql, values)
        logger.info(f"User '{username}' updated with {kwargs}.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error updating user '{username}': {e}")
        st.error(f"Error updating user data: {e}")
        return False

def get_all_students():
    """Retrieves data for all non-admin users."""
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn:
            cursor = conn.execute("SELECT username, current_level, history FROM users WHERE is_admin = 0")
            students = []
            for row in cursor.fetchall():
                student = dict(row)
                history = json.loads(student['history'])
                student['Última Práctica'] = history[-1]['date'] if history else 'N/A'
                # Remove the full history list from this view unless needed later
                del student['history']
                students.append(student)
            return students
    except sqlite3.Error as e:
        logger.error(f"Error getting all students: {e}")
        st.error(f"Error retrieving student list: {e}")
        return []

# --- Initial Database Setup ---
init_db()

# --- Security Functions (Keep from original) ---
def hash_password(password):
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ':' + pwd_hash.hex()

def verify_password(stored_password_with_salt, provided_password):
    try:
        salt_hex, stored_hash_hex = stored_password_with_salt.split(':')
        salt = bytes.fromhex(salt_hex)
        pwd_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return pwd_hash == bytes.fromhex(stored_hash_hex)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False

# --- Cache Functions (Keep from original) ---
def load_cache():
    try:
        with open(CONFIG["CACHE_FILE"], 'rb') as f:
            return pickle.load(f)
    except: # Catching all exceptions during load
        return {}

def save_cache(cache):
    try:
        with open(CONFIG["CACHE_FILE"], 'wb') as f:
            pickle.dump(cache, f)
    except Exception as e:
        logger.error(f"Error saving cache: {e}")

# --- Gemini Configuration (Keep from original) ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    ]
    model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_settings)
except Exception as e:
    st.error(f"Gemini API setup failed: {e}")
    st.stop()

# --- Gemini Content Generation (Keep from original, no changes needed here) ---
def generate_reading_text(level):
    cache = load_cache()
    cache_key = f"level_{level}_{datetime.now().strftime('%Y%m%d')}"
    if cache_key in cache:
        logger.info(f"Using cached text for {cache_key}")
        return cache[cache_key]

    difficulty_map = {
        2: ("muy fácil, A1-A2 CEFR", CONFIG["WORD_RANGES"][2], "una descripción simple de un animal"),
        4: ("fácil, A2-B1 CEFR", CONFIG["WORD_RANGES"][4], "una anécdota breve"),
        6: ("intermedio, B1 CEFR", CONFIG["WORD_RANGES"][6], "un resumen de una noticia sencilla"),
        8: ("intermedio-alto, B2 CEFR", CONFIG["WORD_RANGES"][8], "una explicación de un concepto científico básico"),
        10: ("avanzado, C1 CEFR", CONFIG["WORD_RANGES"][10], "un análisis corto de un tema social")
    }
    # Find the closest or cap the level to available ranges
    mapped_level = min([key for key in CONFIG["WORD_RANGES"].keys() if key >= level] or [max(CONFIG["WORD_RANGES"].keys())], key=lambda x: abs(x-level))
    difficulty_desc, words, topic = difficulty_map[mapped_level]


    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt = f"""
    Eres un experto en ELE para estudiantes de 16-17 años. Genera un texto en español de nivel {difficulty_desc} ({level}/10),
    con {words} palabras, sobre {topic}. Hazlo interesante, educativo y seguro para menores (G-rated).
    Evita temas sensibles y usa un lenguaje claro y adaptado al nivel.
    Para asegurar variedad, considera que esta solicitud se hace en {timestamp}.
    Devuelve solo el texto, sin títulos ni comentarios adicionales.
    """

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            # Basic check to ensure generated text is not just whitespace or too short
            if text and len(text.split()) >= 40: # Arbitrary min word count
                logger.info(f"Generated unique text for level {level} at {timestamp}")
                cache[cache_key] = text
                save_cache(cache)
                return text
            logger.warning(f"Generated text too short or empty on attempt {attempt+1}")
        except Exception as e:
            logger.error(f"Text generation attempt {attempt+1} failed: {e}")
            if attempt < CONFIG["MAX_RETRIES"] - 1:
                time.sleep(1.5 ** (attempt + 1)) # Exponential backoff
    st.error("Failed to generate text after retries.")
    return None

def generate_mc_questions(text):
    json_example = '[{"question": "Pregunta de ejemplo", "options": {"A": "Opción A", "B": "Opción B", "C": "Opción C", "D": "Opción D"}, "correct_answer": "A"}]' # More complete example

    prompt = (
        "Basado solo en este texto:\n"
        "---\n"
        f"{text}\n"
        "---\n"
        "Genera exactamente 5 preguntas de opción múltiple en español con 4 opciones (A, B, C, D) por pregunta, una sola correcta. "
        "Cubre idea principal, detalles clave, inferencias obvias y vocabulario del texto. "
        "Usa un lenguaje claro y opciones plausibles pero distintas. "
        f"Devuelve solo una lista JSON válida. Ejemplo de formato: {json_example}. Asegúrate de que sea solo el JSON, sin texto explicativo."
    )

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = model.generate_content(prompt)
            raw_response = response.text.strip()
            logger.info(f"Raw response from Gemini (questions): {raw_response}")
            # Attempt to clean markdown code blocks from the response
            json_text = re.sub(r'^```json\n|```$', '', raw_response, flags=re.MULTILINE | re.DOTALL).strip()
            questions = json.loads(json_text)
            if isinstance(questions, list) and len(questions) == 5 and all(
                isinstance(q, dict) and
                'question' in q and isinstance(q['question'], str) and
                'options' in q and isinstance(q['options'], dict) and len(q['options']) == 4 and
                'correct_answer' in q and q['correct_answer'] in q['options']
                for q in questions
            ):
                logger.info("Generated questions successfully")
                return questions
            logger.error(f"Invalid question format or count: {questions}")
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
                if user_data and verify_password(user_data["hashed_password_with_salt"], password_input):
                    st.session_state.logged_in = True
                    st.session_state.username = user_data["username"]
                    st.session_state.is_admin = bool(user_data["is_admin"]) # SQLite stores 0/1, convert to bool
                    st.session_state.current_level = user_data["current_level"] if not st.session_state.is_admin else None # Admins don't have a level
                    st.success(f"¡Bienvenido/a {st.session_state.username}!")
                    logger.info(f"Successful login for {st.session_state.username}")
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
                    if add_user(email_input, pwd_input): # Add user to DB
                        st.success("¡Registrado! Ahora puedes iniciar sesión.")
                        logger.info(f"New user registered: {email_input}")
                    else:
                        st.error("Error al registrar usuario.") # Should ideally not happen if add_user returns False only on IntegrityError


else: # User is logged in
    st.sidebar.write(f"Usuario: {st.session_state.username}")
    if st.sidebar.button("Cerrar Sesión"):
        # Save user's current level before logging out if they are a student
        if st.session_state.username and not st.session_state.is_admin:
             # Retrieve current history to save it along with the level (optional, but good practice)
             user_data_before_logout = get_user(st.session_state.username)
             if user_data_before_logout:
                 update_user(st.session_state.username, current_level=st.session_state.current_level, history=user_data_before_logout['history'])
                 logger.info(f"Updated level for {st.session_state.username} to {st.session_state.current_level} on logout")
             else:
                 logger.warning(f"User data not found for {st.session_state.username} during logout save.")


        st.session_state.clear() # Clear all session state variables
        st.session_state.logged_in = False # Explicitly set logged_in to False just in case
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
        if st.button("Crear Copia de Seguridad de la Base de Datos"):
             try:
                os.makedirs(CONFIG["BACKUP_DIR"], exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = os.path.join(CONFIG["BACKUP_DIR"], f"user_data_backup_{timestamp}.db")
                shutil.copy2(CONFIG["DB_FILE"], backup_path) # copy2 preserves metadata
                st.success(f"Copia de seguridad creada: {backup_path}")
                logger.info(f"Database backup created: {backup_path}")
             except Exception as e:
                 st.error(f"Error creando copia de seguridad: {e}")
                 logger.error(f"Error creating database backup: {e}")


    else: # User is a student
        st.title("🚀 Práctica Lectora")
        st.info(f"Nivel actual: {st.session_state.current_level}")

        if st.session_state.current_text is None or st.session_state.current_questions is None:
            # Only show the button to start/continue if no text/questions are loaded
            if st.button("Comenzar" if st.session_state.score == 0 else "Siguiente", type="primary"):
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
                            st.session_state.score = 0 # Reset score for the new text
                            st.session_state.feedback_given = False
                            st.rerun() # Rerun to display the text and questions
                        else:
                             # Clear text if question generation failed
                            st.session_state.current_text = None
                            st.session_state.current_questions = None


        else: # Text and questions are loaded
            st.markdown(f"<div style='background-color:#f0f2f6;padding:15px;border-radius:5px;'>{st.session_state.current_text}</div>", unsafe_allow_html=True)

            st.subheader("Preguntas:")
            # Use a unique key for the form based on the current text/questions
            form_key = f"qa_form_{hash(st.session_state.current_text+json.dumps(st.session_state.current_questions))}"

            with st.form(form_key, clear_on_submit=False): # Use clear_on_submit=False to keep selections visible after submission
                # Store user answers keyed by question index
                user_selections = {}
                for i, q in enumerate(st.session_state.current_questions):
                    options = [f"{k}. {v}" for k, v in q["options"].items()]
                    # Use a unique key for each radio button group
                    selection = st.radio(
                        f"**{i+1}. {q['question']}**",
                        options,
                        key=f"q_{i}_{form_key}", # Ensure unique key per question instance
                        disabled=st.session_state.submitted_answers
                    )
                    # Store the selected option's letter (A, B, C, D)
                    if selection:
                         user_selections[i] = selection[0] # Get the letter before the '.'

                # Store the selections in session state user_answers dictionary
                st.session_state.user_answers = user_selections

                submit_button = st.form_submit_button("Enviar", disabled=st.session_state.submitted_answers)

            if submit_button:
                 st.session_state.submitted_answers = True
                 # Need to re-calculate and display score/feedback *after* form submission state changes
                 st.rerun() # Rerun to update the state and show results below the form


            # This block runs after submitted_answers becomes True
            if st.session_state.submitted_answers:
                # Calculate score based on the stored user_answers and correct answers
                score = 0
                for i, q in enumerate(st.session_state.current_questions):
                    # Check if the question was answered and if the answer is correct
                    if i in st.session_state.user_answers and st.session_state.user_answers[i] == q["correct_answer"]:
                        score += 1

                st.session_state.score = score # Update score in session state

                st.metric("Puntuación", f"{score}/5")

                # Display feedback for each question
                st.subheader("Respuestas:")
                for i, q in enumerate(st.session_state.current_questions):
                    user_answer_letter = st.session_state.user_answers.get(i) # Get the selected letter
                    correct_answer_letter = q["correct_answer"]

                    if user_answer_letter == correct_answer_letter:
                        st.success(f"**{i+1}. Correcto.**")
                    elif user_answer_letter is not None: # Answered, but wrong
                        user_option_text = q["options"].get(user_answer_letter, "Opción no encontrada")
                        correct_option_text = q["options"].get(correct_answer_letter, "Opción no encontrada")
                        st.error(f"**{i+1}. Incorrecto.** Tu respuesta fue '{user_answer_letter}. {user_option_text}'. La respuesta correcta era '{correct_answer_letter}. {correct_option_text}'.")
                    else: # Not answered
                         st.warning(f"**{i+1}. No respondido.** La respuesta correcta era '{correct_answer_letter}. {q['options'].get(correct_answer_letter, 'Opción no encontrada')}'.")


                # Adaptive level adjustment and history saving (only do this once per submission)
                if not st.session_state.feedback_given: # Flag to ensure this runs only once per submission
                    percentage = (score / 5) * 100
                    previous_level = st.session_state.current_level

                    if percentage >= 80 and st.session_state.current_level < CONFIG["MAX_LEVEL"]:
                        st.session_state.current_level += 1
                        st.success(f"¡Excelente! Subes al nivel {st.session_state.current_level}.")
                    elif percentage <= 40 and st.session_state.current_level > CONFIG["MIN_LEVEL"]:
                        st.session_state.current_level -= 1
                        st.warning(f"Necesitas un poco más de práctica. Bajas al nivel {st.session_state.current_level}.")
                    else:
                        st.info(f"Buen intento. Te mantienes en el nivel {st.session_state.current_level}.")

                    # Fetch current user data to append history
                    user_data = get_user(st.session_state.username)
                    if user_data:
                        user_history = user_data.get("history", []) # Get existing history
                        user_history.append({
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), # Add timestamp for more detail
                            "level_before": previous_level, # Log level before this practice
                            "level_after": st.session_state.current_level, # Log level after this practice
                            "score": score,
                            "text_summary": st.session_state.current_text[:100] + "..." # Store a snippet of the text
                        })
                        # Update user record in the database
                        update_user(st.session_state.username, current_level=st.session_state.current_level, history=user_history)
                        logger.info(f"Saved practice result for {st.session_state.username}: Level {previous_level} -> {st.session_state.current_level}, Score {score}/5")
                    else:
                         logger.error(f"Could not find user {st.session_state.username} to save practice history.")
                         st.error("Error saving your progress.")

                    st.session_state.feedback_given = True # Mark feedback as given

                # Button to proceed to the next text
                if st.button("Siguiente Texto"):
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.submitted_answers = False
                    st.session_state.score = 0 # Reset score
                    st.session_state.feedback_given = False
                    st.rerun()

st.caption(f"v{CONFIG['APP_VERSION']} - Desarrollado con Streamlit y Gemini por Moris Polanco | mp@ufm.edu | [morispolanco.vercel.app](https://morispolanco.vercel.app)")
