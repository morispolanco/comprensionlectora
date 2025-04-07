# app.py
import streamlit as st
import google.generativeai as genai
import json
import hashlib
import os
import time
import pandas as pd # Para la vista de admin

# --- Configuración Inicial ---
USER_DATA_FILE = "user_data.json"
MIN_LEVEL = 1
MAX_LEVEL = 10
DEFAULT_LEVEL = 3 # Nivel inicial para nuevos estudiantes

# --- Funciones de Seguridad y Datos de Usuario ---
def hash_password(password):
    """Genera un hash seguro de la contraseña con un salt."""
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ':' + pwd_hash.hex()

def verify_password(stored_password_with_salt, provided_password):
    """Verifica la contraseña proporcionada contra el hash almacenado."""
    try:
        salt_hex, stored_hash_hex = stored_password_with_salt.split(':')
        salt = bytes.fromhex(salt_hex)
        stored_hash = bytes.fromhex(stored_hash_hex)
        pwd_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return pwd_hash == stored_hash
    except (ValueError, IndexError, TypeError): # Manejar varios errores posibles
        print(f"Error interno: Formato de contraseña almacenada inválido o tipo incorrecto.")
        return False
    except Exception as e:
        print(f"Error al verificar contraseña: {e}")
        return False

def load_user_data():
    """Carga los datos de usuario desde el archivo JSON."""
    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f: # Especificar encoding
            content = f.read()
            if not content:
                 # No mostrar error aquí, dejar que se regenere si es necesario
                 print(f"Advertencia: El archivo '{USER_DATA_FILE}' está vacío.")
                 # Proceder a regenerar si es necesario en el bloque FileNotFoundError
                 raise FileNotFoundError # Forzar la lógica de regeneración
            return json.loads(content)
    except FileNotFoundError:
        st.warning(f"Archivo '{USER_DATA_FILE}' no encontrado o vacío. Creando uno nuevo con el usuario admin.")
        admin_user = "mp@ufm.edu"
        admin_pass = "moris123"
        try:
            hashed_admin_pass = hash_password(admin_pass)
            initial_data = {
                admin_user: {
                    "hashed_password_with_salt": hashed_admin_pass,
                    "level": None,
                    "is_admin": True
                }
            }
            save_user_data(initial_data)
            return initial_data
        except Exception as e_regen:
            st.error(f"Error crítico al regenerar '{USER_DATA_FILE}': {e_regen}")
            return {} # Devolver vacío si la regeneración falla
    except json.JSONDecodeError:
        st.error(f"Error Crítico: El archivo '{USER_DATA_FILE}' está corrupto o no es JSON válido. Se necesita intervención manual (borrarlo o arreglarlo).")
        # Se podría intentar renombrar el archivo corrupto aquí
        # try:
        #     corrupt_filename = USER_DATA_FILE + f".corrupt_{int(time.time())}"
        #     os.rename(USER_DATA_FILE, corrupt_filename)
        #     st.warning(f"Se renombró el archivo corrupto a '{corrupt_filename}'. Intenta recargar la página.")
        # except OSError as e_rename:
        #      st.error(f"No se pudo renombrar el archivo corrupto: {e_rename}")
        return {} # Devuelve vacío para evitar más errores
    except Exception as e:
        st.error(f"Error inesperado al cargar datos de usuario: {e}")
        return {}

def save_user_data(data):
    """Guarda los datos de usuario en el archivo JSON."""
    try:
        with open(USER_DATA_FILE, 'w', encoding='utf-8') as f: # Especificar encoding
            json.dump(data, f, indent=4, ensure_ascii=False) # ensure_ascii=False para acentos
    except Exception as e:
        st.error(f"Error Crítico al guardar datos de usuario: {e}")

# --- Configuración de Gemini ---
try:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=gemini_api_key)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    ]
    # Configurar modelo con timeout
    model = genai.GenerativeModel(
        'gemini-1.5-flash',
        safety_settings=safety_settings,
        generation_config=genai.types.GenerationConfig(
             candidate_count=1, # Solo necesitamos una respuesta
             # temperature=0.7 # Puedes ajustar la creatividad
        )
        # Considerar añadir request_options={'timeout': 60} si hay timeouts frecuentes
    )
except KeyError:
    st.error("Error Crítico: No se encontró la clave 'GEMINI_API_KEY' en los secrets de Streamlit (.streamlit/secrets.toml). La aplicación no puede funcionar sin ella.")
    st.stop()
except Exception as e:
    st.error(f"Error crítico al configurar Gemini: {e}")
    st.stop()

# --- Funciones de Generación con Gemini ---

# Reintentos simples para funciones de Gemini
MAX_GEMINI_RETRIES = 2
RETRY_DELAY = 2 # segundos

def generate_reading_text(level):
    """Genera un texto de lectura adaptado al nivel con reintentos."""
    if level <= 2: difficulty_desc, words = "muy fácil, vocabulario simple, frases cortas", "50-80"
    elif level <= 4: difficulty_desc, words = "fácil, vocabulario común, frases relativamente cortas", "80-120"
    elif level <= 6: difficulty_desc, words = "intermedio, vocabulario variado, frases de longitud media", "120-180"
    elif level <= 8: difficulty_desc, words = "desafiante, vocabulario rico, estructuras complejas", "180-250"
    else: difficulty_desc, words = "muy desafiante, vocabulario avanzado, frases largas/complejas", "250-350"

    prompt = f"""
    Eres un asistente educativo creando material de lectura para estudiantes de Quinto Bachillerato (16-17 años) en español.
    Genera un texto corto sobre un tema interesante, educativo y apropiado (ciencia, historia breve, tecnología explicada, arte, sociedad actual, naturaleza). NO uses temas controversiales o delicados.
    Nivel de dificultad: {difficulty_desc} (nivel {level}/{MAX_LEVEL}). Extensión: aprox. {words} palabras.
    El texto debe ser autocontenido, permitir 5 preguntas claras de comprensión y ser seguro para adolescentes.
    NO incluyas título ni preguntas. Solo el texto de lectura.
    IMPORTANTE: El texto generado debe estar EN ESPAÑOL.
    """
    last_exception = None
    for attempt in range(MAX_GEMINI_RETRIES):
        try:
            response = model.generate_content(prompt)
            if response.text and len(response.text) > 30:
                return response.text.strip()
            else: # Respuesta vacía o muy corta
                 print(f"Intento {attempt+1} (Texto): Respuesta vacía o corta. Prompt Feedback: {getattr(response, 'prompt_feedback', 'N/A')}")
                 if response.prompt_feedback and response.prompt_feedback.block_reason:
                      st.warning(f"Generación de texto bloqueada por seguridad: {response.prompt_feedback.block_reason}. Reintentando...")

        except Exception as e:
            print(f"Error en API Gemini (Texto, intento {attempt+1}): {e}")
            last_exception = e
        
        # Esperar antes del siguiente intento (excepto en el último)
        if attempt < MAX_GEMINI_RETRIES - 1:
            time.sleep(RETRY_DELAY)

    st.error(f"Error al generar texto con Gemini después de {MAX_GEMINI_RETRIES} intentos. Último error: {last_exception}")
    return None

def generate_mc_questions(text):
    """Genera 5 preguntas de opción múltiple basadas en el texto con reintentos y validación."""
    prompt = f"""
    Basado en el siguiente texto en español, crea EXACTAMENTE 5 preguntas de opción múltiple (A, B, C, D) para evaluar comprensión lectora de un estudiante de Quinto Bachillerato.
    Requisitos:
    1. Cubrir diferentes aspectos: idea principal, detalles, inferencias simples, vocabulario en contexto.
    2. Preguntas claras y directas en español.
    3. Distractores plausibles pero claramente incorrectos según el texto. Solo UNA opción correcta.
    4. Salida ESTRICTAMENTE como lista JSON válida. Cada objeto: {"{"}"question": "...", "options": {"{"}"A": "...", "B": "...", "C": "...", "D": "..."{"}"}, "correct_answer": "LETRA_MAYUSCULA"{"}"}.

    Texto:
    ---
    {text}
    ---

    Salida JSON (SOLO la lista JSON, sin texto adicional, comentarios, ni markdown):
    """
    last_exception = None
    json_response_text = ""
    for attempt in range(MAX_GEMINI_RETRIES):
        try:
            response = model.generate_content(prompt)
            json_response_text = response.text.strip().lstrip('```json').rstrip('```').strip()
            
            if not json_response_text:
                 print(f"Intento {attempt+1} (Preguntas): Respuesta vacía. Prompt Feedback: {getattr(response, 'prompt_feedback', 'N/A')}")
                 if response.prompt_feedback and response.prompt_feedback.block_reason:
                      st.warning(f"Generación de preguntas bloqueada por seguridad: {response.prompt_feedback.block_reason}. Reintentando...")
                 continue # Reintentar si está vacío

            parsed_data = json.loads(json_response_text)

            # Validación rigurosa de la estructura
            if isinstance(parsed_data, list) and len(parsed_data) == 5:
                valid_structure = True
                for i, q in enumerate(parsed_data):
                    if not isinstance(q, dict) or not all(k in q for k in ["question", "options", "correct_answer"]):
                        print(f"Error validación (Pregunta {i+1}): Faltan claves principales."); valid_structure = False; break
                    if not isinstance(q["options"], dict) or len(q["options"]) != 4 or not all(k in q["options"] for k in ["A", "B", "C", "D"]):
                         print(f"Error validación (Pregunta {i+1}): Formato de opciones incorrecto."); valid_structure = False; break
                    if q["correct_answer"] not in ["A", "B", "C", "D"]:
                        print(f"Error validación (Pregunta {i+1}): Letra de respuesta correcta inválida ('{q['correct_answer']}')."); valid_structure = False; break
                    if not isinstance(q["question"], str) or not all(isinstance(opt, str) for opt in q["options"].values()):
                        print(f"Error validación (Pregunta {i+1}): Texto de pregunta u opción no es string."); valid_structure = False; break
                
                if valid_structure:
                    return parsed_data # Éxito

            else: # No es lista de 5 elementos
                print(f"Error validación (Intento {attempt+1}): No es una lista de 5 elementos (Tipo: {type(parsed_data)}, Longitud: {len(parsed_data) if isinstance(parsed_data, list) else 'N/A'})")

        except json.JSONDecodeError as e:
            print(f"Error JSONDecodeError (Preguntas, intento {attempt+1}): {e}. Respuesta recibida:\n{json_response_text}")
            last_exception = e
        except Exception as e:
            print(f"Error inesperado procesando preguntas (Intento {attempt+1}): {e}")
            last_exception = e
            # Si falla por seguridad, intentar mostrar info
            try:
                 if response.prompt_feedback and response.prompt_feedback.block_reason:
                      print(f"Bloqueo de seguridad detectado: {response.prompt_feedback.block_reason}")
                      st.warning(f"Generación bloqueada por seguridad: {response.prompt_feedback.block_reason}. Reintentando...")
            except Exception: pass # Ignorar si 'response' no existe o no tiene feedback

        # Esperar antes del siguiente intento (excepto en el último)
        if attempt < MAX_GEMINI_RETRIES - 1:
            time.sleep(RETRY_DELAY)

    st.error(f"Error al generar/validar preguntas después de {MAX_GEMINI_RETRIES} intentos.")
    st.text_area("Última respuesta JSON recibida (para depuración):", json_response_text if json_response_text else "Vacía", height=150)
    return None

# --- Información en la Barra Lateral (Siempre visible) ---
st.sidebar.title("📖 Práctica Lectora Adaptativa")
st.sidebar.markdown("""
Esta aplicación usa IA (Gemini 1.5 Flash) para generar textos y preguntas adaptados a tu nivel de comprensión.

**¿Cómo funciona?**
1.  Regístrate o inicia sesión.
2.  Lee el texto y responde las preguntas.
3.  La dificultad se ajustará para el siguiente texto.
¡Practica a tu ritmo!
""")
st.sidebar.divider()
st.sidebar.subheader("Desarrollador")
st.sidebar.info("Moris Polanco")
st.sidebar.write("📧 mp@ufm.edu")
st.sidebar.markdown("🌐 [morispolanco.vercel.app](https://morispolanco.vercel.app)")
st.sidebar.divider()

# --- Inicialización del Estado de la Sesión ---
default_session_state = {
    'logged_in': False, 'username': None, 'is_admin': False,
    'current_level': DEFAULT_LEVEL, 'current_text': None, 'current_questions': None,
    'user_answers': {}, 'submitted_answers': False, 'score': 0, 'feedback_given': False
}
for key, value in default_session_state.items():
    if key not in st.session_state:
        st.session_state[key] = value

# --- Cargar datos de usuario ---
# Es importante cargarlos una vez aquí para las comprobaciones iniciales
user_data = load_user_data()

# ==============================================================================
# --- PANTALLA DE LOGIN / REGISTRO ---
# ==============================================================================
if not st.session_state.logged_in:
    st.title("Bienvenido/a a la Práctica de Comprensión Lectora")

    # Recargar datos aquí por si se creó el archivo en load_user_data
    if not user_data:
         user_data = load_user_data() # Intentar recargar por si se regeneró

    # Verificar si el archivo sigue inutilizable
    if not user_data and os.path.exists(USER_DATA_FILE):
         st.error("El archivo de datos de usuario sigue corrupto o vacío. No se puede continuar.")
         st.stop()
    elif not user_data and not os.path.exists(USER_DATA_FILE):
          st.error("No se pudo crear el archivo inicial de datos de usuario. Verifica los permisos.")
          st.stop()


    auth_choice = st.radio("Selecciona una opción:", ("Iniciar Sesión", "Registrarse"), horizontal=True, key="auth_choice")

    if auth_choice == "Iniciar Sesión":
        st.subheader("Iniciar Sesión")
        with st.form("login_form"):
            username = st.text_input("Usuario (Email)", key="login_user").lower().strip()
            password = st.text_input("Contraseña", type="password", key="login_pass")
            submitted = st.form_submit_button("Entrar")

            if submitted:
                if not username or not password:
                    st.warning("Ingresa usuario y contraseña.")
                elif username in user_data:
                    stored_pass_info = user_data[username].get('hashed_password_with_salt')
                    if stored_pass_info and verify_password(stored_pass_info, password):
                        # --- Inicio de Sesión Exitoso ---
                        st.session_state.logged_in = True
                        st.session_state.username = username
                        st.session_state.is_admin = user_data[username].get('is_admin', False)
                        if not st.session_state.is_admin:
                            st.session_state.current_level = user_data[username].get('level', DEFAULT_LEVEL)
                        else:
                             st.session_state.current_level = None # Admin no tiene nivel
                        # Resetear estado del juego
                        st.session_state.current_text = None
                        st.session_state.current_questions = None
                        st.session_state.user_answers = {}
                        st.session_state.submitted_answers = False
                        st.session_state.score = 0
                        st.session_state.feedback_given = False
                        st.success(f"¡Bienvenido/a {username}!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Usuario o contraseña incorrectos.")
                else:
                    st.error("Usuario o contraseña incorrectos.")

    elif auth_choice == "Registrarse":
        st.subheader("Registrar Nuevo Usuario (Estudiante)")
        with st.form("register_form"):
            new_username = st.text_input("Nuevo Usuario (Tu Email)", key="reg_user").lower().strip()
            new_password = st.text_input("Nueva Contraseña", type="password", key="reg_pass")
            confirm_password = st.text_input("Confirmar Contraseña", type="password", key="reg_confirm")
            submitted = st.form_submit_button("Registrarme")

            if submitted:
                # Validaciones
                is_valid = True
                if not new_username or not new_password or not confirm_password:
                    st.warning("Completa todos los campos.")
                    is_valid = False
                if "@" not in new_username or "." not in new_username:
                     st.error("Usa un formato de email válido para el usuario.")
                     is_valid = False
                if len(new_password) < 6:
                     st.error("La contraseña debe tener al menos 6 caracteres.")
                     is_valid = False
                if new_password != confirm_password:
                    st.error("Las contraseñas no coinciden.")
                    is_valid = False
                # Comprobar si ya existe DESPUÉS de otras validaciones
                if is_valid and new_username in user_data:
                    st.error("Este email ya está registrado. Intenta iniciar sesión.")
                    is_valid = False

                if is_valid:
                    # --- Registro Exitoso ---
                    hashed_pass = hash_password(new_password)
                    user_data[new_username] = {
                        "hashed_password_with_salt": hashed_pass,
                        "level": DEFAULT_LEVEL,
                        "is_admin": False
                    }
                    save_user_data(user_data)
                    st.success(f"¡Usuario '{new_username}' registrado! Ahora selecciona 'Iniciar Sesión'.")
                    time.sleep(2)
                    # Podríamos cambiar automáticamente a 'Iniciar Sesión' si quisiéramos
                    # st.session_state.auth_choice = "Iniciar Sesión"
                    # st.rerun()


# ==============================================================================
# --- PANTALLA PRINCIPAL (USUARIO LOGUEADO) ---
# ==============================================================================
else:
    # --- Barra Lateral para Usuario Logueado ---
    def perform_logout_and_rerun():
        """Guarda estado si es necesario, limpia sesión y recarga."""
        user_data_logout = load_user_data()
        username_logout = st.session_state.get('username')
        is_admin_logout = st.session_state.get('is_admin', False)

        if not is_admin_logout and username_logout and username_logout in user_data_logout:
                current_level_to_save = st.session_state.get('current_level')
                if current_level_to_save is not None:
                     user_data_logout[username_logout]['level'] = current_level_to_save
                     save_user_data(user_data_logout)

        keys_to_clear = list(default_session_state.keys()) # Usar las claves por defecto
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]

        st.session_state.logged_in = False # Asegurar estado explícito
        st.rerun()

    st.sidebar.header(f"Sesión Activa:")
    st.sidebar.write(f"Usuario: **{st.session_state.username}**")
    if not st.session_state.is_admin:
        st.sidebar.write(f"Nivel Práctica: **{st.session_state.current_level}**")

    if st.sidebar.button("❌ Cerrar Sesión Actual", key="logout_button"):
        perform_logout_and_rerun()

    st.sidebar.divider()
    st.sidebar.markdown("**Otras Opciones:**")
    if st.sidebar.button("🗝️ Cambiar Usuario / Iniciar Sesión", key="switch_user_button"):
        st.toast("Cerrando sesión actual...") # Toast es menos intrusivo
        perform_logout_and_rerun()

    if st.sidebar.button("📝 Registrar Nuevo Usuario", key="register_new_button"):
        st.toast("Cerrando sesión actual...")
        perform_logout_and_rerun()
    # --- Fin Barra Lateral ---


    # --- Contenido Principal ---
    # --- Vista de Administrador ---
    if st.session_state.is_admin:
        st.title("📊 Panel de Administración")
        st.write("Progreso de los estudiantes registrados:")
        user_data_admin = load_user_data() # Recargar datos frescos
        student_data = []
        for user, data in user_data_admin.items():
            if not data.get('is_admin', False):
                student_data.append({
                    "Usuario (Email)": user,
                    "Nivel Actual": data.get('level', 'N/A')
                })
        if student_data:
            df = pd.DataFrame(student_data).sort_values(by="Usuario (Email)").reset_index(drop=True)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Aún no hay estudiantes registrados.")

    # --- Vista de Estudiante ---
    else:
        st.title("🚀 Práctica de Comprensión Lectora 🚀")
        #st.info(f"Nivel actual: **{st.session_state.current_level}** ({MIN_LEVEL}-{MAX_LEVEL})") # Redundante con sidebar
        #st.markdown("---")

        # --- Lógica de Generación / Inicio ---
        if st.session_state.current_text is None or st.session_state.current_questions is None:
            st.markdown("---")
            st.info("¡Listo/a para practicar!")
            if st.button("✨ Generar Texto y Preguntas ✨", key="generate_content", type="primary"):
                level_to_generate = st.session_state.current_level
                with st.spinner(f"Generando material para nivel {level_to_generate}... ⏳"):
                    new_text = generate_reading_text(level_to_generate)
                    if new_text:
                        new_questions = generate_mc_questions(new_text)
                        if new_questions:
                            st.session_state.current_text = new_text
                            st.session_state.current_questions = new_questions
                            st.session_state.user_answers = {}
                            st.session_state.submitted_answers = False
                            st.session_state.score = 0
                            st.session_state.feedback_given = False
                            st.rerun()
                        else:
                            st.error("Error al generar preguntas. Inténtalo de nuevo.")
                            # Limpiar ambos si las preguntas fallan
                            st.session_state.current_text = None
                            st.session_state.current_questions = None
                    else:
                        st.error("Error al generar texto. Inténtalo de nuevo.")
                        st.session_state.current_text = None
                        st.session_state.current_questions = None


        # --- Mostrar Texto y Preguntas ---
        elif st.session_state.current_text and st.session_state.current_questions:
            st.markdown("---")
            st.subheader("📖 Lee el texto:")
            st.markdown(f"> {st.session_state.current_text}") # Usar blockquote para destacar
            # st.text_area("Texto", st.session_state.current_text, height=200, disabled=True, label_visibility="collapsed")
            st.markdown("---")
            st.subheader("🤔 Responde las preguntas:")

            with st.form("qa_form"):
                temp_answers = {}
                for i, q in enumerate(st.session_state.current_questions):
                    options_list = list(q["options"].values())
                    options_dict = q["options"]
                    radio_key = f"q_{i}_{st.session_state.current_level}_{hash(st.session_state.current_text)}" # Key más única

                    # Determinar índice seleccionado actual (si ya envió)
                    current_selection_index = None
                    if st.session_state.submitted_answers:
                         selected_letter = st.session_state.user_answers.get(i)
                         if selected_letter:
                             selected_text = options_dict.get(selected_letter)
                             if selected_text in options_list:
                                 try: current_selection_index = options_list.index(selected_text)
                                 except ValueError: pass

                    selected_option_text = st.radio(
                        label=f"**{i+1}. {q['question']}**",
                        options=options_list,
                        key=radio_key,
                        index=current_selection_index,
                        disabled=st.session_state.submitted_answers
                    )

                    selected_letter = None
                    if selected_option_text:
                        for letter, text in options_dict.items():
                            if text == selected_option_text: selected_letter = letter; break
                    temp_answers[i] = selected_letter

                submit_button = st.form_submit_button("✔️ Enviar Respuestas", disabled=st.session_state.submitted_answers)

                if submit_button and not st.session_state.submitted_answers:
                    if None in temp_answers.values():
                         st.warning("Por favor, responde todas las preguntas.")
                    else:
                        st.session_state.user_answers = temp_answers
                        st.session_state.submitted_answers = True
                        st.session_state.feedback_given = False
                        st.rerun()

            # --- Mostrar Feedback y Resultados ---
            if st.session_state.submitted_answers:
                st.markdown("---")
                st.subheader("📊 Resultados de esta Ronda")

                correct_count = 0
                results_display = []
                questions_data = st.session_state.current_questions
                answers_data = st.session_state.user_answers

                for i, q in enumerate(questions_data):
                    user_ans_letter = answers_data.get(i)
                    correct_ans_letter = q["correct_answer"]
                    options_dict = q["options"]
                    is_correct = (user_ans_letter == correct_ans_letter)
                    if is_correct: correct_count += 1

                    result_text = f"**{i+1}. {q['question']}**\n"
                    if user_ans_letter:
                         result_text += f"*   Tu respuesta: **{user_ans_letter}**. _{options_dict.get(user_ans_letter, 'Opción inválida')}_"
                    else:
                         result_text += f"*   No respondiste."

                    if is_correct: result_text += " (Correcto ✔️)"
                    else: result_text += f" (Incorrecto ❌ - Correcta: **{correct_ans_letter}**. _{options_dict.get(correct_ans_letter, '???')}_)"
                    results_display.append(result_text)

                st.session_state.score = correct_count
                score_percentage = (correct_count / len(questions_data)) * 100
                st.metric(label="Puntuación", value=f"{correct_count} / {len(questions_data)}", delta=f"{score_percentage:.0f}%")

                with st.expander("Ver detalle de respuestas"):
                    for item in results_display:
                         st.markdown(item); st.markdown("---")

                # --- Lógica de Adaptación de Nivel ---
                if not st.session_state.feedback_given:
                    previous_level = st.session_state.current_level
                    level_changed = False
                    user_data_level = load_user_data() # Cargar datos frescos para guardar nivel

                    if score_percentage >= 80: # 4 o 5 correctas
                        if st.session_state.current_level < MAX_LEVEL:
                            st.session_state.current_level += 1; level_changed = True
                            st.success("¡Muy bien! Aumentando dificultad.")
                        else: st.success("¡Excelente! ¡Nivel máximo alcanzado!")
                    elif score_percentage < 40: # 0 o 1 correcta
                         if st.session_state.current_level > MIN_LEVEL:
                             st.session_state.current_level -= 1; level_changed = True
                             st.warning("Vamos a probar un nivel más sencillo.")
                         else: st.info("¡Sigue practicando en este nivel!")
                    else: # 2 o 3 correctas (40% - 60%)
                         st.info("¡Buen intento! Mantenemos el nivel.")

                    if level_changed:
                         st.write(f"Nuevo nivel de práctica: **{st.session_state.current_level}**")
                         username_level = st.session_state.username
                         if username_level in user_data_level:
                             user_data_level[username_level]['level'] = st.session_state.current_level
                             save_user_data(user_data_level)
                         else: st.error("Error al guardar nivel: usuario no encontrado.")

                    st.session_state.feedback_given = True

                # --- Botón para Siguiente Texto ---
                if st.button("➡️ Siguiente Texto", key="next_text_button"):
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    # Mantener user_answers, submitted_answers, score hasta la nueva generación
                    st.rerun()

# --- Footer ---
st.markdown("---")
st.caption("Aplicación de Práctica Lectora Adaptativa v1.2 | Moris Polanco")
