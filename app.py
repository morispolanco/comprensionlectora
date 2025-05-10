import streamlit as st
import json
import hashlib
import os
import time
import pandas as pd
import logging
from logging.handlers import RotatingFileHandler
import shutil
from datetime import datetime
import re
import pickle
import sqlite3
import requests  # Para llamadas a OpenRouter

# --- Configuración ---
CONFIG = {
    "MIN_LEVEL": 1,
    "MAX_LEVEL": 10,
    "DEFAULT_LEVEL": 3,
    "MAX_RETRIES": 3,
    "WORD_RANGES": {2: "50-80", 4: "80-120", 6: "120-180", 8: "180-250", 10: "250-350"},
    "DB_FILE": "user_data.db",
    "LOG_FILE": "app.log",
    "BACKUP_DIR": "backups",
    "APP_VERSION": "1.5.0",
    "CACHE_FILE": "text_cache.pkl",
    "OPENROUTER_API_KEY": st.secrets["OPENROUTER_API_KEY"],
    "OPENROUTER_MODEL": "meta-llama/llama-4-maverick:free"
}

# Cargar credenciales de administrador
try:
    ADMIN_USER = st.secrets["ADMIN_USER"]
    ADMIN_PASS = st.secrets["ADMIN_PASS"]
except KeyError as e:
    st.error(f"Falta la clave secreta: {e}")
    st.stop()

# Configurar logging
handler = RotatingFileHandler(CONFIG["LOG_FILE"], maxBytes=10 * 1024 * 1024, backupCount=5)
logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# --- Funciones de Seguridad ---
def hash_password(password):
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 310000)
    return salt.hex() + ':' + pwd_hash.hex()


def verify_password(stored, provided):
    try:
        salt_hex, stored_hash_hex = stored.split(':')
        salt = bytes.fromhex(salt_hex)
        pwd_hash = hashlib.pbkdf2_hmac('sha256', provided.encode('utf-8'), salt, 310000)
        return pwd_hash == bytes.fromhex(stored_hash_hex)
    except Exception as e:
        logger.error(f"Error al verificar contraseña: {e}")
        return False


# --- Funciones de Base de Datos ---
def get_db_connection():
    try:
        conn = sqlite3.connect(CONFIG["DB_FILE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error conectando a la base de datos: {e}")
        st.error(f"Error conectando a la base de datos: {e}")
        return None


def init_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY UNIQUE,
                    hashed_password_with_salt TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    current_level INTEGER DEFAULT 3,
                    history TEXT NOT NULL DEFAULT '[]'
                )
            """)
        admin_user_data = get_user(ADMIN_USER)
        if not admin_user_data:
            hashed_pass = hash_password(ADMIN_PASS)
            conn.execute(
                "INSERT INTO users (username, hashed_password_with_salt, is_admin, current_level, history) VALUES (?, ?, ?, ?, ?)",
                (ADMIN_USER, hashed_pass, 1, None, json.dumps([])))
            logger.info(f"Usuario admin '{ADMIN_USER}' creado.")
    except sqlite3.Error as e:
        logger.error(f"Error inicializando la base de datos: {e}")


def get_user(username):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
        user_data = cursor.fetchone()
        if user_data:
            user_dict = dict(user_data)
            try:
                user_dict['history'] = json.loads(user_dict['history'])
            except json.JSONDecodeError:
                user_dict['history'] = []
            return user_dict
        return None
    finally:
        conn.close()


def add_user(username, password, is_admin=False, level=CONFIG["DEFAULT_LEVEL"]):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        hashed_pass = hash_password(password)
        with conn:
            conn.execute(
                "INSERT INTO users (username, hashed_password_with_salt, is_admin, current_level, history) VALUES (?, ?, ?, ?, ?)",
                (username, hashed_pass, 1 if is_admin else 0, level, json.dumps([])))
        logger.info(f"Usuario '{username}' agregado.")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"Intento de agregar usuario duplicado '{username}'.")
        return False
    finally:
        conn.close()


def update_user(username, **kwargs):
    conn = get_db_connection()
    if not conn or not kwargs:
        return False
    set_clauses = []
    values = []
    for key, value in kwargs.items():
        if key == 'history':
            values.append(json.dumps(value, ensure_ascii=False))
        else:
            values.append(value)
        set_clauses.append(f"{key} = ?")
    sql = f"UPDATE users SET {', '.join(set_clauses)} WHERE username = ?"
    values.append(username)
    try:
        with conn:
            conn.execute(sql, values)
        logger.info(f"Usuario '{username}' actualizado.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error actualizando usuario '{username}': {e}")
        return False
    finally:
        conn.close()


def get_all_students():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.execute("SELECT username, current_level, history FROM users WHERE is_admin = 0")
        students = []
        for row in cursor.fetchall():
            student = dict(row)
            try:
                history = json.loads(student['history'])
                student['Última Práctica'] = history[-1]['date'].split(' ')[0] if history else 'N/A'
            except (json.JSONDecodeError, KeyError, IndexError):
                student['Última Práctica'] = 'N/A'
            del student['history']
            students.append(student)
        return students
    except sqlite3.Error as e:
        logger.error(f"Error obteniendo estudiantes: {e}")
        return []
    finally:
        conn.close()


# --- Funciones de Caché ---
def load_cache():
    try:
        if os.path.exists(CONFIG["CACHE_FILE"]):
            with open(CONFIG["CACHE_FILE"], 'rb') as f:
                return pickle.load(f)
        return {}
    except Exception as e:
        logger.error(f"Error cargando caché: {e}")
        return {}


def save_cache(cache):
    try:
        with open(CONFIG["CACHE_FILE"], 'wb') as f:
            pickle.dump(cache, f)
    except Exception as e:
        logger.error(f"Error guardando caché: {e}")


# --- Generación de Texto usando OpenRouter ---
def generate_reading_text(level):
    cache = load_cache()
    cache_key = f"level_{level}_{datetime.now().strftime('%Y%m%d')}"
    if cache_key in cache:
        logger.info(f"Usando texto de caché para {cache_key}")
        return cache[cache_key]

    level_keys = sorted(CONFIG["WORD_RANGES"].keys())
    mapped_level_key = min(level_keys, key=lambda x: abs(x - level))
    difficulty_map = {
        2: ("muy fácil, A1-A2 CEFR", CONFIG["WORD_RANGES"].get(2, "50-80"), "una descripción simple de un animal o mascota"),
        4: ("fácil, A2-B1 CEFR", CONFIG["WORD_RANGES"].get(4, "80-120"), "una anécdota breve de la vida cotidiana"),
        6: ("intermedio, B1 CEFR", CONFIG["WORD_RANGES"].get(6, "120-180"), "un resumen de una noticia sencilla o evento histórico corto"),
        8: ("intermedio-alto, B2 CEFR", CONFIG["WORD_RANGES"].get(8, "180-250"), "explicación de un concepto científico básico"),
        10: ("avanzado, C1 CEFR", CONFIG["WORD_RANGES"].get(10, "250-350"), "análisis corto de un tema social o cultural")
    }

    difficulty_desc, words, topic = difficulty_map.get(mapped_level_key, difficulty_map[max(level_keys)])
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prompt = f"""
    Eres un experto en ELE para estudiantes de 16-17 años. Genera un texto en español de nivel {difficulty_desc},
    con {words} palabras, sobre {topic}. Hazlo interesante, educativo y seguro para menores.
    Evita temas sensibles, lenguaje inapropiado o controversias. Usa lenguaje claro adaptado al nivel.
    Considera esta solicitud hecha en {timestamp}.
    Devuelve solo el texto, sin títulos ni comentarios adicionales.
    """

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions ",
                headers={
                    "Authorization": f"Bearer {CONFIG['OPENROUTER_API_KEY']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": CONFIG["OPENROUTER_MODEL"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 512
                }
            )

            if response.status_code == 200:
                result = response.json()
                text = result["choices"][0]["message"]["content"].strip()
                word_count = len(text.split())
                min_words = int(words.split('-')[0])

                if text and word_count >= min_words * 0.8 and len(text) > 100:
                    logger.info(f"Texto generado (len={word_count}) para nivel {level} a las {timestamp}")
                    cache[cache_key] = text
                    save_cache(cache)
                    return text
                else:
                    logger.warning(f"Texto muy corto o vacío en intento {attempt + 1}")
            else:
                logger.error(f"Solicitud a OpenRouter fallida con código {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Generación de texto fallida en intento {attempt+1}: {e}")
            if attempt < CONFIG["MAX_RETRIES"] - 1:
                time.sleep(1.5 ** (attempt + 1))

    st.error("No se pudo generar el texto después de varios intentos.")
    return None


# --- Generación de Preguntas usando OpenRouter ---
def generate_mc_questions(text):
    json_example = '[{"question": "Pregunta de ejemplo", "options": {"A": "Opción A", "B": "Opción B", "C": "Opción C", "D": "Opción D"}, "correct_answer": "A"}]'
    prompt = f"""
    Basado solo en este texto:
---
{text}
---
Genera exactamente 5 preguntas de opción múltiple en español con 4 opciones (A, B, C, D), una correcta por pregunta.
Cubre idea principal, detalles clave, inferencias obvias y vocabulario del texto.
Devuelve solo una lista JSON válida. Ejemplo de formato: {json_example}. Solo el JSON, sin explicaciones adicionales.
"""

    for attempt in range(CONFIG["MAX_RETRIES"]):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions ",
                headers={
                    "Authorization": f"Bearer {CONFIG['OPENROUTER_API_KEY']}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": CONFIG["OPENROUTER_MODEL"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 1024
                }
            )

            if response.status_code == 200:
                raw_response = response.json()["choices"][0]["message"]["content"].strip()
                logger.info(f"Respuesta bruta de OpenRouter (preguntas): {raw_response}")

                # Limpiar posibles bloques de código
                json_text = re.sub(r'^```json\n|\n```$', '', raw_response, flags=re.MULTILINE | re.DOTALL).strip()
                questions = json.loads(json_text)

                # Validar estructura
                if isinstance(questions, list) and len(questions) == 5 and all(
                    isinstance(q, dict) and
                    'question' in q and isinstance(q['question'], str) and q['question'].strip() != "" and
                    'options' in q and isinstance(q['options'], dict) and len(q['options']) == 4 and
                    'correct_answer' in q and isinstance(q['correct_answer'], str) and q['correct_answer'].strip().upper() in ['A', 'B', 'C', 'D'] and
                    all(isinstance(opt, str) and opt.strip() != "" for opt in q['options'].values())
                    for q in questions
                ):
                    for q in questions:
                        q['correct_answer'] = q['correct_answer'].strip().upper()
                    logger.info("Preguntas generadas correctamente.")
                    return questions
                logger.error(f"Formato de preguntas inválido o cantidad incorrecta: {questions}. Raw: {raw_response}")
            else:
                logger.error(f"Solicitud a OpenRouter fallida con código {response.status_code}: {response.text}")
        except json.JSONDecodeError as e:
            logger.error(f"No se pudo analizar el JSON en el intento {attempt+1}: {e}. Raw: {raw_response}")
        except Exception as e:
            logger.error(f"Generación de preguntas fallida en intento {attempt+1}: {e}")
        if attempt < CONFIG["MAX_RETRIES"] - 1:
            time.sleep(1.5 ** (attempt + 1))

    st.error("No se pudieron generar preguntas después de varios intentos.")
    return None


# --- Inicialización de la base de datos ---
init_db()

# --- Estado de sesión ---
default_state = {
    'logged_in': False, 'username': None, 'is_admin': False, 'current_level': CONFIG["DEFAULT_LEVEL"],
    'current_text': None, 'current_questions': None, 'user_answers': {}, 'submitted_answers': False,
    'score': 0, 'feedback_given': False, 'current_view': 'Práctica'
}
for key, val in default_state.items():
    if key not in st.session_state:
        st.session_state[key] = val

# --- Interfaz de login/registro ---
if not st.session_state.logged_in:
    st.title("Bienvenido/a")
    auth_choice = st.radio("Opción:", ("Iniciar Sesión", "Registrarse"), horizontal=True)
    if auth_choice == "Iniciar Sesión":
        with st.form("login_form"):
            username_input = st.text_input("Email").lower().strip()
            password_input = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Entrar")
            if submitted:
                user_data = get_user(username_input)
                if user_data and verify_password(user_data["hashed_password_with_salt"], password_input):
                    st.session_state.logged_in = True
                    st.session_state.username = user_data["username"]
                    st.session_state.is_admin = bool(user_data["is_admin"])
                    db_level = user_data.get("current_level")
                    st.session_state.current_level = db_level if db_level is not None and not st.session_state.is_admin else CONFIG["DEFAULT_LEVEL"]
                    st.success(f"¡Bienvenido/a {st.session_state.username}!")
                    logger.info(f"Inicio de sesión exitoso para {st.session_state.username}")
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas.")
    else:
        with st.form("register_form"):
            email_input = st.text_input("Email").lower().strip()
            pwd_input = st.text_input("Contraseña", type="password")
            confirm_input = st.text_input("Confirmar", type="password")
            if st.form_submit_button("Registrar"):
                if not re.match(r"[^@]+@[^@]+\.[^@]+", email_input):
                    st.error("Formato de correo inválido.")
                elif pwd_input != confirm_input:
                    st.error("Las contraseñas no coinciden.")
                elif len(pwd_input) < 8 or not any(c.isupper() for c in pwd_input) or not any(c.isdigit() for c in pwd_input):
                    st.error("La contraseña debe tener 8+ caracteres, una mayúscula y un número.")
                elif get_user(email_input):
                    st.error("Correo ya registrado.")
                else:
                    if add_user(email_input, pwd_input):
                        st.success("¡Registrado! Ahora puedes iniciar sesión.")
                        logger.info(f"Nuevo usuario registrado: {email_input}")
                    else:
                        st.error("Error al registrar usuario.")

# --- Panel principal si está logueado ---
else:
    st.sidebar.write(f"Usuario: {st.session_state.username}")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state.clear()
        st.rerun()

    if st.session_state.is_admin:
        st.title("Panel de Administración")
        st.warning("Los administradores no pueden acceder al modo práctica.")
        st.subheader("Lista de Estudiantes")
        students = get_all_students()
        if students:
            df_students = pd.DataFrame(students)
            df_students = df_students.rename(columns={'username': 'Email', 'current_level': 'Nivel'})
            st.dataframe(df_students)
        else:
            st.info("No hay estudiantes registrados.")

    else:
        st.title("🚀 Práctica Lectora")
        st.info(f"Nivel actual: {st.session_state.current_level}")
        progress_value = (st.session_state.current_level - CONFIG["MIN_LEVEL"]) / (CONFIG["MAX_LEVEL"] - CONFIG["MIN_LEVEL"])
        st.progress(progress_value, text=f"Nivel: {st.session_state.current_level}/{CONFIG['MAX_LEVEL']}")

        if st.session_state.current_text is None or st.session_state.current_questions is None:
            button_text = "Comenzar" if st.session_state.score == 0 else "Siguiente"
            if st.button(button_text, type="primary"):
                st.session_state.score = 0
                st.session_state.feedback_given = False
                with st.spinner("Preparando texto..."):
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
                            st.session_state.current_text = None
                            st.session_state.current_questions = None
                            st.error("No se pudieron generar preguntas.")
                    else:
                        st.session_state.current_text = None
                        st.session_state.current_questions = None
                        st.error("No se pudo generar texto.")
        else:
            st.markdown(f"<div style='background-color:#f0f2f6;padding:15px;border-radius:5px;'>{st.session_state.current_text}</div>", unsafe_allow_html=True)
            st.subheader("Preguntas:")

            form_key = f"qa_form_{hash(st.session_state.current_text + json.dumps(st.session_state.current_questions, sort_keys=True))}"
            is_submitted = st.session_state.submitted_answers

            with st.container():
                with st.form(form_key, clear_on_submit=False):
                    user_selections = {}
                    for i, q in enumerate(st.session_state.current_questions):
                        options = [f"{k}. {v}" for k, v in q["options"].items()]
                        radio_key = f"q_{i}_{form_key}"
                        default_index = None
                        if is_submitted and i in st.session_state.user_answers:
                            prev_answer_letter = st.session_state.user_answers[i]
                            if prev_answer_letter in q['options']:
                                default_index = next((j for j, opt_str in enumerate(options) if opt_str.startswith(prev_answer_letter)), None)
                        selection = st.radio(
                            f"**{i+1}. {q['question']}**",
                            options,
                            index=default_index,
                            key=radio_key,
                            disabled=is_submitted
                        )
                        if selection:
                            user_selections[i] = selection[0]
                    st.session_state.user_answers = user_selections
                    submit_button = st.form_submit_button("Enviar", disabled=is_submitted)

            if is_submitted:
                score = 0
                for i, q in enumerate(st.session_state.current_questions):
                    if i in st.session_state.user_answers and st.session_state.user_answers[i] == q["correct_answer"]:
                        score += 1
                st.session_state.score = score
                st.metric("Puntuación", f"{score}/5")

                st.subheader("Respuestas:")
                for i, q in enumerate(st.session_state.current_questions):
                    user_answer_letter = st.session_state.user_answers.get(i)
                    correct_answer_letter = q["correct_answer"]
                    correct_option_text = q['options'].get(correct_answer_letter, "Opción no encontrada")
                    if user_answer_letter:
                        user_option_text = q["options"].get(user_answer_letter, "Opción no encontrada")
                        if user_answer_letter == correct_answer_letter:
                            st.success(f"**{i+1}. Correcto.**")
                        else:
                            st.error(f"**{i+1}. Incorrecto.** Tu respuesta fue '{user_answer_letter}. {user_option_text}'. La correcta era '{correct_answer_letter}. {correct_option_text}'.")
                    else:
                        st.warning(f"**{i+1}. No respondido.** La correcta era '{correct_answer_letter}. {correct_option_text}'.")

                if not st.session_state.feedback_given:
                    percentage = (score / 5) * 100
                    previous_level = st.session_state.current_level
                    if percentage >= 80 and st.session_state.current_level < CONFIG["MAX_LEVEL"]:
                        st.session_state.current_level += 1
                        st.balloons()
                        st.success(f"¡Excelente! Subes al nivel {st.session_state.current_level}.")
                    elif percentage <= 40 and st.session_state.current_level > CONFIG["MIN_LEVEL"]:
                        st.session_state.current_level -= 1
                        st.warning(f"Baja al nivel {st.session_state.current_level}. Necesitas más práctica.")
                    else:
                        st.info(f"Mantienes el nivel {st.session_state.current_level}.")

                    user_data = get_user(st.session_state.username)
                    if user_data:
                        user_history = user_data.get("history", [])
                        user_history.append({
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "level_before_practice": previous_level,
                            "level_after_practice": st.session_state.current_level,
                            "score": score,
                            "text_snippet": st.session_state.current_text[:150] + "..."
                        })
                        update_user(st.session_state.username, current_level=st.session_state.current_level, history=user_history)
                        logger.info(f"Historial guardado para {st.session_state.username}")
                    st.session_state.feedback_given = True

                if st.button("Siguiente Texto"):
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.user_answers = {}
                    st.session_state.submitted_answers = False
                    st.session_state.score = 0
                    st.session_state.feedback_given = False
                    st.rerun()

# Footer
st.caption(f"v{CONFIG['APP_VERSION']} - Desarrollado con Streamlit y OpenRouter por Moris Polanco | mp@ufm.edu | [morispolanco.vercel.app](https://morispolanco.vercel.app )")
