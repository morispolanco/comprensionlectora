import streamlit as st
import google.generativeai as genai
import json
import hashlib
import os
import time
import pandas as pd
import logging
import pickle
import shutil
from datetime import datetime

# --- Configuration ---
CONFIG = {
    "MIN_LEVEL": 1,
    "MAX_LEVEL": 10,
    "DEFAULT_LEVEL": 3,
    "MAX_RETRIES": 3,
    "WORD_RANGES": {2: "50-80", 4: "80-120", 6: "120-180", 8: "180-250", 10: "250-350"},
    "USER_DATA_FILE": "user_data.json",
    "CACHE_FILE": "text_cache.pkl",
    "LOG_FILE": "app.log",
    "BACKUP_DIR": "backups"
}

# Load admin credentials from Streamlit secrets
try:
    ADMIN_USER = st.secrets["ADMIN_USER"]
    ADMIN_PASS = st.secrets["ADMIN_PASS"]
except KeyError as e:
    st.error(f"Missing required secret: {e}. Please add it to .streamlit/secrets.toml.")
    st.stop()

# Setup logging
logging.basicConfig(
    filename=CONFIG["LOG_FILE"],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Security and Data Functions ---
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

def load_user_data():
    if not os.path.exists(CONFIG["USER_DATA_FILE"]):
        logger.warning(f"{CONFIG['USER_DATA_FILE']} not found. Creating with admin.")
        initial_data = {
            ADMIN_USER: {
                "hashed_password_with_salt": hash_password(ADMIN_PASS),
                "level": None,
                "is_admin": True,
                "history": []
            }
        }
        save_user_data(initial_data)
        return initial_data
    
    try:
        with open(CONFIG["USER_DATA_FILE"], 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if data else {}
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Error loading user data: {e}")
        st.error(f"User data corrupted: {e}. Restoring from backup or resetting.")
        return restore_from_backup() or {}

def save_user_data(data):
    try:
        os.makedirs(CONFIG["BACKUP_DIR"], exist_ok=True)
        if os.path.exists(CONFIG["USER_DATA_FILE"]):
            backup_path = os.path.join(CONFIG["BACKUP_DIR"], f"user_data_{int(time.time())}.json")
            shutil.copy(CONFIG["USER_DATA_FILE"], backup_path)
        temp_file = CONFIG["USER_DATA_FILE"] + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, CONFIG["USER_DATA_FILE"])
        os.chmod(CONFIG["USER_DATA_FILE"], 0o600)  # Restrict permissions
    except Exception as e:
        logger.error(f"Error saving user data: {e}")
        st.error(f"Failed to save data: {e}")

def restore_from_backup():
    backups = sorted([f for f in os.listdir(CONFIG["BACKUP_DIR"]) if f.startswith("user_data_")], reverse=True)
    if backups:
        latest_backup = os.path.join(CONFIG["BACKUP_DIR"], backups[0])
        try:
            with open(latest_backup, 'r', encoding='utf-8') as f:
                data = json.load(f)
            save_user_data(data)
            logger.info(f"Restored from backup: {latest_backup}")
            return data
        except Exception as e:
            logger.error(f"Backup restore failed: {e}")
    return {}

# --- Cache Management ---
def load_cache():
    if os.path.exists(CONFIG["CACHE_FILE"]):
        with open(CONFIG["CACHE_FILE"], 'rb') as f:
            return pickle.load(f)
    return {}

def save_cache(cache):
    with open(CONFIG["CACHE_FILE"], 'wb') as f:
        pickle.dump(cache, f)

# --- Gemini Configuration ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
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

# --- Gemini Content Generation ---
def generate_reading_text(level):
    cache = load_cache()
    cache_key = f"level_{level}"
    if cache_key in cache:
        logger.info(f"Loaded text from cache for level {level}")
        return cache[cache_key]

    difficulty_map = {
        2: ("muy fácil, A1-A2 CEFR", CONFIG["WORD_RANGES"][2], "una descripción simple de un animal"),
        4: ("fácil, A2-B1 CEFR", CONFIG["WORD_RANGES"][4], "una anécdota breve"),
        6: ("intermedio, B1 CEFR", CONFIG["WORD_RANGES"][6], "un resumen de una noticia sencilla"),
        8: ("intermedio-alto, B2 CEFR", CONFIG["WORD_RANGES"][8], "una explicación de un concepto científico básico"),
        10: ("avanzado, C1 CEFR", CONFIG["WORD_RANGES"][10], "un análisis corto de un tema social")
    }
    difficulty_desc, words, topic = difficulty_map.get(min(level, 10), difficulty_map[10])

    # Refined prompt for clarity and consistency
    prompt = f"""
    Eres un experto en ELE para estudiantes de 16-17 años. Genera un texto en español de nivel {difficulty_desc} ({level}/10), 
    con {words} palabras, sobre {topic}. Hazlo interesante, educativo y seguro para menores (G-rated). 
    Evita temas sensibles y usa un lenguaje claro y adaptado al nivel. Devuelve solo el texto, sin títulos ni comentarios adicionales.
    """
    
    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            if len(text) > 40:  # Basic validation
                cache[cache_key] = text
                save_cache(cache)
                logger.info(f"Generated text for level {level}")
                return text
        except Exception as e:
            logger.error(f"Text generation attempt {attempt+1} failed: {e}")
            if attempt < CONFIG["MAX_RETRIES"] - 1:
                time.sleep(1.5 ** attempt)
    st.error("Failed to generate text after retries.")
    return None

def generate_mc_questions(text):
    # Tweaked prompt for improved question quality
    prompt = f"""
    Basado solo en este texto:
    ---
    {text}
    ---
    Genera exactamente 5 preguntas de opción múltiple en español con 4 opciones (A-D), una sola correcta. 
    Cubre idea principal, detalles clave, inferencias obvias y vocabulario del texto. 
    Usa un lenguaje claro y opciones plausibles pero distintas. 
    Devuelve solo una lista JSON válida: [{"question": "", "options": {"A": "", "B": "", "C": "", "D": ""}, "correct_answer": ""}]
    """
    
    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = model.generate_content(prompt)
            json_text = response.text.strip().replace("```json", "").replace("```", "")
            questions = json.loads(json_text)
            if isinstance(questions, list) and len(questions) == 5:
                logger.info("Generated questions successfully")
                return questions
        except Exception as e:
            logger.error(f"Questions generation attempt {attempt+1} failed: {e}")
            if attempt < CONFIG["MAX_RETRIES"] - 1:
                time.sleep(1.5 ** attempt)
    st.error("Failed to generate questions after retries.")
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
default_state = {
    'logged_in': False, 'username': None, 'is_admin': False, 'current_level': CONFIG["DEFAULT_LEVEL"],
    'current_text': None, 'current_questions': None, 'user_answers': {}, 'submitted_answers': False,
    'score': 0, 'feedback_given': False
}
for key, value in default_state.items():
    if key not in st.session_state:
        st.session_state[key] = value

# --- Authentication ---
user_data = load_user_data()

if not st.session_state.logged_in:
    st.title("Bienvenido/a")
    auth_choice = st.radio("Opción:", ("Iniciar Sesión", "Registrarse"), horizontal=True)

    if auth_choice == "Iniciar Sesión":
        with st.form("login_form"):
            username = st.text_input("Email").lower().strip()
            password = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Entrar"):
                if username in user_data and verify_password(user_data[username]["hashed_password_with_salt"], password):
                    st.session_state.logged_in = True
                    st.session_state.username = username
                    st.session_state.is_admin = user_data[username].get("is_admin", False)
                    st.session_state.current_level = user_data[username].get("level", CONFIG["DEFAULT_LEVEL"]) if not st.session_state.is_admin else None
                    st.success("¡Bienvenido/a!")
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas.")

    else:
        with st.form("register_form"):
            email = st.text_input("Email").lower().strip()
            pwd = st.text_input("Contraseña", type="password")
            confirm = st.text_input("Confirmar", type="password")
            if st.form_submit_button("Registrar"):
                if pwd != confirm or len(pwd) < 8 or not any(c.isupper() for c in pwd) or not any(c.isdigit() for c in pwd):
                    st.error("Contraseña debe tener 8+ caracteres, mayúscula y número.")
                elif email in user_data:
                    st.error("Usuario ya registrado.")
                else:
                    user_data[email] = {"hashed_password_with_salt": hash_password(pwd), "level": CONFIG["DEFAULT_LEVEL"], "is_admin": False, "history": []}
                    save_user_data(user_data)
                    st.success("¡Registrado! Inicia sesión.")

else:
    st.sidebar.write(f"Usuario: {st.session_state.username}")
    if st.sidebar.button("Cerrar Sesión"):
        if not st.session_state.is_admin:
            user_data[st.session_state.username]["level"] = st.session_state.current_level
            save_user_data(user_data)
        st.session_state.clear()
        st.session_state.logged_in = False
        st.rerun()

    if st.session_state.is_admin:
        st.title("Panel de Administración")
        students = [{"Email": u, "Nivel": d["level"], "Última Práctica": d["history"][-1]["date"] if d["history"] else "N/A"} 
                    for u, d in user_data.items() if not d.get("is_admin", False)]
        if students:
            st.dataframe(pd.DataFrame(students))
        else:
            st.info("No hay estudiantes.")

    else:
        st.title("🚀 Práctica Lectora")
        st.info(f"Nivel actual: {st.session_state.current_level}")

        if st.session_state.current_text is None:
            if st.button("Comenzar" if st.session_state.score == 0 else "Siguiente", type="primary"):
                with st.spinner("Preparando un texto interesante…"):
                    text = generate_reading_text(st.session_state.current_level)
                    if text:
                        questions = generate_mc_questions(text)
                        if questions:
                            st.session_state.current_text = text
                            st.session_state.current_questions = questions
                            st.session_state.user_answers = {}
                            st.session_state.submitted_answers = False
                            st.rerun()

        else:
            st.markdown(f"<div style='background-color:#f0f2f6;padding:15px'>{st.session_state.current_text}</div>", unsafe_allow_html=True)
            with st.form("qa_form"):
                for i, q in enumerate(st.session_state.current_questions):
                    options = [f"{k}. {v}" for k, v in q["options"].items()]
                    st.radio(f"**{i+1}. {q['question']}**", options, key=f"q_{i}", disabled=st.session_state.submitted_answers)
                if st.form_submit_button("Enviar", disabled=st.session_state.submitted_answers):
                    st.session_state.submitted_answers = True
                    st.rerun()

            if st.session_state.submitted_answers:
                score = sum(1 for i, q in enumerate(st.session_state.current_questions) if st.session_state[f"q_{i}"][0] == q["correct_answer"])
                st.session_state.score = score
                st.metric("Puntuación", f"{score}/5")
                
                percentage = (score / 5) * 100
                if percentage >= 80 and st.session_state.current_level < CONFIG["MAX_LEVEL"]:
                    st.session_state.current_level += 1
                    st.success(f"¡Subes al nivel {st.session_state.current_level}!")
                elif percentage <= 40 and st.session_state.current_level > CONFIG["MIN_LEVEL"]:
                    st.session_state.current_level -= 1
                    st.warning(f"Bajas al nivel {st.session_state.current_level}")
                
                user_data[st.session_state.username]["level"] = st.session_state.current_level
                user_data[st.session_state.username]["history"].append({"date": datetime.now().strftime("%Y-%m-%d"), "level": st.session_state.current_level, "score": score})
                save_user_data(user_data)

                if st.button("Siguiente Texto"):
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.submitted_answers = False
                    st.rerun()

# Footer with creator info
st.caption("v1.2.0 - Desarrollado con Streamlit y Gemini por Moris Polanco | mp@ufm.edu | [morispolanco.vercel.app](https://morispolanco.vercel.app)")
