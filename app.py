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
    except (ValueError, IndexError): # Si el split falla o falta el formato
        st.error("Error interno: Formato de contraseña almacenada inválido.")
        return False
    except Exception as e:
        st.error(f"Error al verificar contraseña: {e}")
        return False

def load_user_data():
    """Carga los datos de usuario desde el archivo JSON."""
    try:
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        st.warning(f"Archivo '{USER_DATA_FILE}' no encontrado. Creando uno nuevo.")
        # Si el archivo se borra, recreamos al admin por seguridad
        admin_user = "mp@ufm.edu"
        admin_pass = "moris123"
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
    except json.JSONDecodeError:
        st.error(f"Error: El archivo '{USER_DATA_FILE}' está corrupto o vacío. Se necesita intervención manual.")
        return {} # Devuelve vacío para evitar más errores
    except Exception as e:
        st.error(f"Error inesperado al cargar datos de usuario: {e}")
        return {}

def save_user_data(data):
    """Guarda los datos de usuario en el archivo JSON."""
    try:
        with open(USER_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        st.error(f"Error al guardar datos de usuario: {e}")

# --- Configuración de Gemini ---
try:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    # print("Gemini Model Initialized") # Debugging
except KeyError:
    st.error("Error: No se encontró la clave 'GEMINI_API_KEY' en los secrets de Streamlit.")
    st.stop() # Detiene la ejecución si no hay API key
except Exception as e:
    st.error(f"Error al configurar Gemini: {e}")
    st.stop()

# --- Funciones de Generación con Gemini ---

def generate_reading_text(level):
    """Genera un texto de lectura adaptado al nivel."""
    # Ajustar la descripción del nivel para la IA
    if level <= 2:
        difficulty_desc = "muy fácil, con vocabulario simple y frases cortas"
        words = "50-80"
    elif level <= 4:
        difficulty_desc = "fácil, con vocabulario común y frases relativamente cortas"
        words = "80-120"
    elif level <= 6:
        difficulty_desc = "intermedio, con algo de vocabulario variado y frases de longitud media"
        words = "120-180"
    elif level <= 8:
        difficulty_desc = "desafiante, con vocabulario más rico y estructuras de frases complejas"
        words = "180-250"
    else:
        difficulty_desc = "muy desafiante, con vocabulario avanzado y frases largas y complejas"
        words = "250-350"

    prompt = f"""
    Eres un asistente educativo creando material de lectura para estudiantes de Quinto Bachillerato (aproximadamente 16-17 años).
    Genera un texto corto de lectura sobre un tema interesante y apropiado para esa edad (ciencia, historia, tecnología, arte, sociedad, etc.).
    El nivel de dificultad debe ser {difficulty_desc} (nivel {level} de {MAX_LEVEL}).
    El texto debe tener entre {words} palabras.
    El texto debe ser autocontenido y permitir formular preguntas de comprensión sobre él.
    NO incluyas preguntas ni el título en el texto, solo el párrafo o párrafos de la lectura.
    """
    try:
        response = model.generate_content(prompt)
        # print(f"Gemini Text Response: {response.text}") # Debugging
        return response.text.strip()
    except Exception as e:
        st.error(f"Error al generar texto con Gemini: {e}")
        return None # Devuelve None si hay error

def generate_mc_questions(text):
    """Genera 5 preguntas de opción múltiple basadas en el texto."""
    prompt = f"""
    Basado en el siguiente texto, crea exactamente 5 preguntas de opción múltiple (A, B, C, D) para evaluar la comprensión lectora de un estudiante de Quinto Bachillerato.
    Asegúrate de que las preguntas cubran diferentes aspectos del texto (idea principal, detalles específicos, inferencias si es posible).
    Las opciones incorrectas (distractores) deben ser plausibles pero claramente incorrectas según el texto.
    Formatea la salida ESTRICTAMENTE como una lista JSON de objetos. Cada objeto debe tener las claves "question" (string), "options" (un diccionario con "A", "B", "C", "D" como claves y los textos de las opciones como valores), y "correct_answer" (string con la letra de la opción correcta, ej. "A", "B", "C", o "D").

    Texto:
    ---
    {text}
    ---

    Salida JSON:
    """
    try:
        response = model.generate_content(prompt)
        # Limpiar la respuesta de Gemini (a veces incluye ```json ... ```)
        # print(f"Gemini Questions Raw Response:\n{response.text}") # Debugging
        json_response_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        # print(f"Gemini Questions Cleaned Response:\n{json_response_text}") # Debugging
        questions = json.loads(json_response_text)
        # Validar estructura básica
        if isinstance(questions, list) and len(questions) == 5:
            for q in questions:
                if not all(k in q for k in ["question", "options", "correct_answer"]):
                    raise ValueError("Formato de pregunta inválido.")
                if not isinstance(q["options"], dict) or len(q["options"]) != 4:
                     raise ValueError("Formato de opciones inválido.")
                if q["correct_answer"] not in ["A", "B", "C", "D"]:
                    raise ValueError("Letra de respuesta correcta inválida.")
            # print(f"Parsed Questions: {questions}") # Debugging
            return questions
        else:
            st.error(f"Error: Gemini no devolvió 5 preguntas en el formato esperado. Se recibieron {len(questions) if isinstance(questions, list) else 'un formato no lista'}.")
            return None
    except json.JSONDecodeError as e:
        st.error(f"Error al decodificar la respuesta JSON de Gemini para las preguntas: {e}")
        st.text_area("Respuesta recibida (para depuración):", json_response_text, height=150)
        return None
    except Exception as e:
        st.error(f"Error al generar preguntas con Gemini o procesar respuesta: {e}")
        st.text_area("Respuesta recibida (para depuración):", response.text if 'response' in locals() else "No response object", height=150)
        return None

# --- Inicialización del Estado de la Sesión ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.is_admin = False
    st.session_state.current_level = DEFAULT_LEVEL
    st.session_state.current_text = None
    st.session_state.current_questions = None
    st.session_state.user_answers = {}
    st.session_state.submitted_answers = False
    st.session_state.score = 0
    st.session_state.feedback_given = False

# --- Lógica de Autenticación y Registro ---
user_data = load_user_data() # Cargar datos al inicio

if not st.session_state.logged_in:
    st.title("Bienvenido a la Práctica de Comprensión Lectora")
    
    auth_choice = st.radio("Selecciona una opción:", ("Iniciar Sesión", "Registrarse"))

    if auth_choice == "Iniciar Sesión":
        st.subheader("Iniciar Sesión")
        with st.form("login_form"):
            username = st.text_input("Usuario (Email)", key="login_user")
            password = st.text_input("Contraseña", type="password", key="login_pass")
            submitted = st.form_submit_button("Entrar")

            if submitted:
                if not username or not password:
                    st.warning("Por favor, ingresa usuario y contraseña.")
                elif username in user_data:
                    # Verificar contraseña
                    stored_pass_info = user_data[username]['hashed_password_with_salt']
                    if verify_password(stored_pass_info, password):
                        st.session_state.logged_in = True
                        st.session_state.username = username
                        st.session_state.is_admin = user_data[username].get('is_admin', False)
                        if not st.session_state.is_admin:
                            st.session_state.current_level = user_data[username].get('level', DEFAULT_LEVEL)
                        # Resetear estado del juego al iniciar sesión
                        st.session_state.current_text = None
                        st.session_state.current_questions = None
                        st.session_state.user_answers = {}
                        st.session_state.submitted_answers = False
                        st.session_state.score = 0
                        st.session_state.feedback_given = False
                        st.success(f"¡Bienvenido {username}!")
                        time.sleep(1) # Pequeña pausa para ver el mensaje
                        st.rerun() # Recarga la página para mostrar el contenido correcto
                    else:
                        st.error("Usuario o contraseña incorrectos.")
                else:
                    st.error("Usuario o contraseña incorrectos.")

    elif auth_choice == "Registrarse":
        st.subheader("Registrar Nuevo Usuario")
        with st.form("register_form"):
            new_username = st.text_input("Nuevo Usuario (Email)", key="reg_user")
            new_password = st.text_input("Nueva Contraseña", type="password", key="reg_pass")
            confirm_password = st.text_input("Confirmar Contraseña", type="password", key="reg_confirm")
            submitted = st.form_submit_button("Registrar")

            if submitted:
                if not new_username or not new_password or not confirm_password:
                    st.warning("Por favor, completa todos los campos.")
                elif new_password != confirm_password:
                    st.error("Las contraseñas no coinciden.")
                elif new_username in user_data:
                    st.error("Este nombre de usuario ya existe. Por favor, elige otro.")
                elif "@" not in new_username or "." not in new_username: # Validación simple de email
                     st.error("Por favor, usa un formato de email válido para el usuario.")
                else:
                    # Registrar nuevo usuario
                    hashed_pass = hash_password(new_password)
                    user_data[new_username] = {
                        "hashed_password_with_salt": hashed_pass,
                        "level": DEFAULT_LEVEL,
                        "is_admin": False
                    }
                    save_user_data(user_data)
                    st.success(f"¡Usuario '{new_username}' registrado con éxito! Ahora puedes iniciar sesión.")
                    time.sleep(2)
                    # No redirigimos automáticamente, dejamos que elijan "Iniciar Sesión"

# --- Contenido Principal (Si está logueado) ---
else:
    st.sidebar.header(f"Usuario: {st.session_state.username}")
    if st.sidebar.button("Cerrar Sesión"):
        # Guardar nivel actual antes de salir (si no es admin)
        if not st.session_state.is_admin and st.session_state.username in user_data:
             user_data[st.session_state.username]['level'] = st.session_state.current_level
             save_user_data(user_data)

        # Limpiar todo el session state relacionado al usuario
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        # Reinicializar estado básico para evitar errores
        st.session_state.logged_in = False
        st.rerun()

    # --- Vista de Administrador ---
    if st.session_state.is_admin:
        st.title("Panel de Administración")
        st.write("Datos de los estudiantes registrados:")

        student_data = []
        for user, data in user_data.items():
            if not data.get('is_admin', False):
                student_data.append({
                    "Usuario": user,
                    "Nivel Actual": data.get('level', 'N/A')
                    # NO MOSTRAR CONTRASEÑAS (ni hasheadas)
                })
        
        if student_data:
            df = pd.DataFrame(student_data)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Aún no hay estudiantes registrados.")

    # --- Vista de Estudiante ---
    else:
        st.title("🚀 Práctica de Comprensión Lectora 🚀")
        st.info(f"Tu nivel actual: **{st.session_state.current_level}** (de {MIN_LEVEL} a {MAX_LEVEL})")
        st.markdown("---")

        # --- Generación de Texto y Preguntas ---
        if st.session_state.current_text is None and st.session_state.current_questions is None:
            with st.spinner(f"Generando nuevo texto y preguntas para el nivel {st.session_state.current_level}..."):
                # print(f"Generating text for level {st.session_state.current_level}") # Debugging
                new_text = generate_reading_text(st.session_state.current_level)
                if new_text:
                    # print(f"Text generated, generating questions...") # Debugging
                    new_questions = generate_mc_questions(new_text)
                    if new_questions:
                        # print("Questions generated successfully.") # Debugging
                        st.session_state.current_text = new_text
                        st.session_state.current_questions = new_questions
                        st.session_state.user_answers = {} # Reiniciar respuestas para nuevo texto
                        st.session_state.submitted_answers = False # Permitir nuevo envío
                        st.session_state.score = 0
                        st.session_state.feedback_given = False
                        st.rerun() # Volver a ejecutar para mostrar el texto/preguntas
                    else:
                        st.error("No se pudieron generar las preguntas. Inténtalo de nuevo más tarde.")
                        # Podríamos añadir un botón para reintentar aquí
                else:
                    st.error("No se pudo generar el texto. Inténtalo de nuevo más tarde.")
                     # Podríamos añadir un botón para reintentar aquí


        # --- Mostrar Texto y Preguntas ---
        if st.session_state.current_text and st.session_state.current_questions:
            st.subheader("📖 Lee el siguiente texto:")
            st.markdown(st.session_state.current_text)
            st.markdown("---")
            st.subheader("🤔 Responde las preguntas:")

            # Usar un formulario para agrupar las preguntas y el botón de envío
            with st.form("qa_form"):
                temp_answers = {}
                for i, q in enumerate(st.session_state.current_questions):
                    options = list(q["options"].values())
                    # Crear etiquetas únicas para cada radio button dentro del bucle
                    radio_key = f"q_{i}_{st.session_state.current_level}" 
                    selected_option_text = st.radio(
                        f"**{i+1}. {q['question']}**",
                        options=options,
                        key=radio_key,
                        # Deshabilitar si ya se enviaron las respuestas
                        disabled=st.session_state.submitted_answers
                    )
                    # Guardar la letra de la opción seleccionada (A, B, C, D)
                    # Encontrar qué letra corresponde al texto seleccionado
                    selected_letter = None
                    for letter, text in q["options"].items():
                        if text == selected_option_text:
                            selected_letter = letter
                            break
                    temp_answers[i] = selected_letter # Usamos índice como clave temporal

                submit_button = st.form_submit_button("Enviar Respuestas", disabled=st.session_state.submitted_answers)

                if submit_button and not st.session_state.submitted_answers:
                    st.session_state.user_answers = temp_answers
                    st.session_state.submitted_answers = True
                    st.session_state.feedback_given = False # Marcar para dar feedback ahora
                    st.rerun() # Re-ejecutar para mostrar feedback

            # --- Mostrar Feedback y Resultados ---
            if st.session_state.submitted_answers:
                st.markdown("---")
                st.subheader("📊 Resultados")
                
                correct_count = 0
                results_display = [] # Para mostrar feedback detallado

                for i, q in enumerate(st.session_state.current_questions):
                    user_ans = st.session_state.user_answers.get(i)
                    correct_ans = q["correct_answer"]
                    is_correct = (user_ans == correct_ans)
                    if is_correct:
                        correct_count += 1
                    
                    # Preparar feedback visual
                    result_text = f"**Pregunta {i+1}:** {q['question']}\n"
                    if user_ans:
                         result_text += f"*   Tu respuesta: **{user_ans}**. {q['options'][user_ans]}"
                    else:
                         result_text += f"*   No respondiste."
                         
                    if is_correct:
                        result_text += " (Correcto ✔️)"
                    else:
                        result_text += f" (Incorrecto ❌ - La correcta era: **{correct_ans}**. {q['options'][correct_ans]})"
                    results_display.append(result_text)
                
                st.session_state.score = correct_count
                
                # Mostrar score general
                st.metric(label="Respuestas Correctas", value=f"{st.session_state.score} de {len(st.session_state.current_questions)}")

                # Mostrar feedback detallado
                with st.expander("Ver detalle de respuestas"):
                    for result_item in results_display:
                         st.markdown(result_item)
                
                # --- Lógica de Adaptación de Nivel ---
                if not st.session_state.feedback_given: # Solo ajustar nivel una vez por set
                    previous_level = st.session_state.current_level
                    if st.session_state.score >= 4: # Buen desempeño -> Subir nivel
                        st.session_state.current_level = min(st.session_state.current_level + 1, MAX_LEVEL)
                        st.success("¡Excelente trabajo! Aumentando la dificultad.")
                    elif st.session_state.score <= 1: # Bajo desempeño -> Bajar nivel
                        st.session_state.current_level = max(st.session_state.current_level - 1, MIN_LEVEL)
                        st.warning("Vamos a probar un texto un poco más sencillo.")
                    else: # Desempeño medio -> Mantener nivel
                        st.info("¡Buen intento! Mantendremos este nivel de dificultad.")

                    if st.session_state.current_level != previous_level:
                         st.write(f"Nuevo nivel: **{st.session_state.current_level}**")
                         # Guardar el nuevo nivel inmediatamente
                         user_data[st.session_state.username]['level'] = st.session_state.current_level
                         save_user_data(user_data)
                    
                    st.session_state.feedback_given = True # Marcar que el feedback/ajuste ya se hizo

                # --- Botón para Siguiente Texto ---
                if st.button("Siguiente Texto"):
                    # Limpiar estado para generar nuevo contenido
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.user_answers = {}
                    st.session_state.submitted_answers = False
                    st.session_state.score = 0
                    st.session_state.feedback_given = False
                    st.rerun() # Recargar para generar nuevo contenido

        elif not st.session_state.current_text and not st.session_state.current_questions and st.session_state.logged_in and not st.session_state.is_admin :
             # Estado inicial o después de un error de generación
             st.info("Haz clic en el botón para empezar tu práctica.")
             if st.button("Generar mi primer texto"):
                  st.rerun() # Esto disparará la lógica de generación al inicio del bloque de estudiante


# --- Footer o información adicional ---
st.markdown("---")
st.caption("Aplicación de práctica de lectura - Desarrollada con Streamlit y Gemini")
