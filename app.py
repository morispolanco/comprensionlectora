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
    except (ValueError, IndexError): # Si el split falla o falta el formato
        # No mostramos error directamente al usuario aquí para no dar pistas
        print(f"Error interno: Formato de contraseña almacenada inválido para {stored_password_with_salt}")
        return False
    except Exception as e:
        print(f"Error al verificar contraseña: {e}") # Loguear el error para el desarrollador
        return False

def load_user_data():
    """Carga los datos de usuario desde el archivo JSON."""
    try:
        with open(USER_DATA_FILE, 'r') as f:
            # Asegurarse de que el archivo no está completamente vacío
            content = f.read()
            if not content:
                 st.error(f"Error: El archivo '{USER_DATA_FILE}' está vacío. Se necesita intervención manual o borrarlo para regenerar al admin.")
                 return {}
            return json.loads(content)
    except FileNotFoundError:
        st.warning(f"Archivo '{USER_DATA_FILE}' no encontrado. Creando uno nuevo con el usuario admin.")
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
        st.error(f"Error: El archivo '{USER_DATA_FILE}' está corrupto o no es JSON válido. Se necesita intervención manual.")
        # Podrías intentar renombrar el archivo corrupto aquí para permitir la regeneración
        # Ejemplo: os.rename(USER_DATA_FILE, USER_DATA_FILE + ".corrupt")
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
    # Especificar generación segura para evitar contenido inapropiado
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    ]
    model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_settings)
    # print("Gemini Model Initialized") # Debugging
except KeyError:
    st.error("Error Crítico: No se encontró la clave 'GEMINI_API_KEY' en los secrets de Streamlit (archivo .streamlit/secrets.toml). La aplicación no puede funcionar sin ella.")
    st.stop() # Detiene la ejecución si no hay API key
except Exception as e:
    st.error(f"Error crítico al configurar Gemini: {e}")
    st.stop()

# --- Funciones de Generación con Gemini ---

def generate_reading_text(level):
    """Genera un texto de lectura adaptado al nivel."""
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
    Eres un asistente educativo creando material de lectura para estudiantes de Quinto Bachillerato (aproximadamente 16-17 años) en español.
    Genera un texto corto de lectura sobre un tema interesante, educativo y apropiado para esa edad (ej: ciencia, historia breve, tecnología explicada, arte, sociedad actual, naturaleza).
    El nivel de dificultad de lectura debe ser {difficulty_desc} (nivel {level} de {MAX_LEVEL}).
    El texto debe tener aproximadamente entre {words} palabras.
    El texto debe ser autocontenido y permitir formular 5 preguntas claras de comprensión sobre él. Debe tener sustancia suficiente para ello.
    NO incluyas un título ni las preguntas en el texto, solo el párrafo o párrafos de la lectura.
    Asegúrate que el texto sea seguro y apropiado para adolescentes.
    """
    try:
        # Añadimos un reintento simple
        for attempt in range(2):
            response = model.generate_content(prompt)
            # print(f"Gemini Text Response (Attempt {attempt+1}): {response.text}") # Debugging
            if response.text and len(response.text) > 30: # Check básico si generó algo
                return response.text.strip()
            time.sleep(1) # Esperar un segundo antes de reintentar
        st.error("Error al generar texto con Gemini después de 2 intentos.")
        return None
    except Exception as e:
        st.error(f"Error en la llamada a la API de Gemini para generar texto: {e}")
        # Podrías querer ver la respuesta completa si falla por seguridad, etc.
        # try:
        #     st.error(f"Prompt Feedback: {response.prompt_feedback}")
        # except Exception: pass
        return None

def generate_mc_questions(text):
    """Genera 5 preguntas de opción múltiple basadas en el texto."""
    prompt = f"""
    Basado en el siguiente texto en español, crea exactamente 5 preguntas de opción múltiple (A, B, C, D) para evaluar la comprensión lectora de un estudiante de Quinto Bachillerato.
    Asegúrate de que:
    1. Las preguntas cubran diferentes aspectos del texto (idea principal, detalles específicos, inferencias simples si el texto lo permite, vocabulario en contexto).
    2. Las preguntas sean claras y directas.
    3. Las opciones incorrectas (distractores) sean plausibles pero claramente incorrectas según el texto proporcionado. No deben ser ambiguas.
    4. Solo una opción sea la correcta.
    5. El idioma sea español.
    Formatea la salida ESTRICTAMENTE como una lista JSON válida. Cada elemento de la lista debe ser un objeto JSON con las siguientes claves EXACTAS:
      - "question": (string) El texto de la pregunta.
      - "options": (objeto JSON) Un diccionario con exactamente cuatro claves: "A", "B", "C", "D". Los valores deben ser los textos (string) de cada opción.
      - "correct_answer": (string) La letra MAYÚSCULA de la opción correcta (ej. "A", "B", "C", o "D").

    Texto:
    ---
    {text}
    ---

    Salida JSON (solo la lista JSON, sin texto adicional antes o después):
    """
    json_response_text = "" # Inicializar para el bloque except
    try:
        # Añadimos un reintento simple
        questions = None
        for attempt in range(2):
            response = model.generate_content(prompt)
            # print(f"Gemini Questions Raw Response (Attempt {attempt+1}):\n{response.text}") # Debugging
            try:
                # Intenta limpiar y parsear
                json_response_text = response.text.strip().lstrip('```json').rstrip('```').strip()
                parsed_data = json.loads(json_response_text)

                # Validar estructura
                if isinstance(parsed_data, list) and len(parsed_data) == 5:
                    valid_structure = True
                    for q in parsed_data:
                        if not isinstance(q, dict) or not all(k in q for k in ["question", "options", "correct_answer"]):
                            valid_structure = False; break
                        if not isinstance(q["options"], dict) or len(q["options"]) != 4 or not all(k in q["options"] for k in ["A", "B", "C", "D"]):
                             valid_structure = False; break
                        if q["correct_answer"] not in ["A", "B", "C", "D"]:
                            valid_structure = False; break
                    if valid_structure:
                        questions = parsed_data
                        # print(f"Parsed Questions: {questions}") # Debugging
                        break # Salir del bucle de reintento si es exitoso
            except json.JSONDecodeError as e:
                print(f"Intento {attempt+1}: JSONDecodeError - {e}")
                # No hacer nada, el bucle reintentará si quedan intentos
            except Exception as e:
                 print(f"Intento {attempt+1}: Error inesperado validando - {e}")
                 # No hacer nada, el bucle reintentará si quedan intentos
            
            if questions is None and attempt < 1: # Si falló y quedan intentos
                 time.sleep(1) # Esperar antes de reintentar
                 
        if questions:
            return questions
        else:
            st.error("Error: Gemini no devolvió 5 preguntas en el formato JSON esperado después de 2 intentos.")
            st.text_area("Última respuesta recibida (para depuración):", json_response_text if json_response_text else "No response text", height=150)
            return None

    except Exception as e:
        st.error(f"Error crítico al generar/procesar preguntas con Gemini: {e}")
        st.text_area("Respuesta recibida (si hubo):", response.text if 'response' in locals() and hasattr(response, 'text') else "No response object", height=150)
        # Podrías querer ver la respuesta completa si falla por seguridad, etc.
        # try:
        #     st.error(f"Prompt Feedback: {response.prompt_feedback}")
        # except Exception: pass
        return None

# --- Información en la Barra Lateral (Siempre visible) ---
st.sidebar.title("📖 Práctica Lectora Adaptativa")
st.sidebar.markdown("""
Esta aplicación utiliza Inteligencia Artificial (Gemini 1.5 Flash) para generar textos y preguntas
adaptados a tu nivel de comprensión lectora.

**¿Cómo funciona?**
1.  **Regístrate** o inicia sesión.
2.  Lee el **texto** proporcionado.
3.  Responde las **preguntas** de opción múltiple.
4.  La aplicación **ajustará la dificultad** para el siguiente texto según tus resultados.
¡Practica a tu propio ritmo!
""")
st.sidebar.divider() # Separador visual
st.sidebar.subheader("Desarrollador")
st.sidebar.info("Moris Polanco")
st.sidebar.write("📧 mp@ufm.edu")
# Usamos markdown para crear un enlace clickeable
st.sidebar.markdown("🌐 [morispolanco.vercel.app](https://morispolanco.vercel.app)")
st.sidebar.divider()

# --- Inicialización del Estado de la Sesión ---
# Necesario para mantener el estado entre interacciones del usuario
default_session_state = {
    'logged_in': False,
    'username': None,
    'is_admin': False,
    'current_level': DEFAULT_LEVEL,
    'current_text': None,
    'current_questions': None,
    'user_answers': {},
    'submitted_answers': False,
    'score': 0,
    'feedback_given': False
}
for key, value in default_session_state.items():
    if key not in st.session_state:
        st.session_state[key] = value

# --- Lógica de Autenticación y Registro ---
user_data = load_user_data() # Cargar datos al inicio

if not st.session_state.logged_in:
    st.title("Bienvenido/a a la Práctica de Comprensión Lectora")

    auth_choice = st.radio("Selecciona una opción:", ("Iniciar Sesión", "Registrarse"), horizontal=True)

    if auth_choice == "Iniciar Sesión":
        st.subheader("Iniciar Sesión")
        with st.form("login_form"):
            username = st.text_input("Usuario (Email)", key="login_user").lower().strip() # Normalizar email
            password = st.text_input("Contraseña", type="password", key="login_pass")
            submitted = st.form_submit_button("Entrar")

            if submitted:
                if not username or not password:
                    st.warning("Por favor, ingresa usuario y contraseña.")
                elif username in user_data:
                    # Verificar contraseña
                    stored_pass_info = user_data[username].get('hashed_password_with_salt')
                    if stored_pass_info and verify_password(stored_pass_info, password):
                        st.session_state.logged_in = True
                        st.session_state.username = username
                        st.session_state.is_admin = user_data[username].get('is_admin', False)
                        if not st.session_state.is_admin:
                            st.session_state.current_level = user_data[username].get('level', DEFAULT_LEVEL)
                        else:
                             st.session_state.current_level = None # Admin no tiene nivel

                        # Resetear estado del juego al iniciar sesión
                        st.session_state.current_text = None
                        st.session_state.current_questions = None
                        st.session_state.user_answers = {}
                        st.session_state.submitted_answers = False
                        st.session_state.score = 0
                        st.session_state.feedback_given = False

                        st.success(f"¡Bienvenido/a {username}!")
                        time.sleep(1) # Pequeña pausa para ver el mensaje
                        st.rerun() # Recarga la página para mostrar el contenido correcto
                    else:
                        st.error("Usuario o contraseña incorrectos.")
                else:
                    st.error("Usuario o contraseña incorrectos.")

    elif auth_choice == "Registrarse":
        st.subheader("Registrar Nuevo Usuario (Estudiante)")
        with st.form("register_form"):
            new_username = st.text_input("Nuevo Usuario (Email)", key="reg_user").lower().strip() # Normalizar email
            new_password = st.text_input("Nueva Contraseña", type="password", key="reg_pass")
            confirm_password = st.text_input("Confirmar Contraseña", type="password", key="reg_confirm")
            submitted = st.form_submit_button("Registrar")

            if submitted:
                # Validaciones
                if not new_username or not new_password or not confirm_password:
                    st.warning("Por favor, completa todos los campos.")
                elif new_password != confirm_password:
                    st.error("Las contraseñas no coinciden.")
                elif "@" not in new_username or "." not in new_username: # Validación muy simple de email
                     st.error("Por favor, usa un formato de email válido para el usuario.")
                elif len(new_password) < 6: # Validación simple de longitud de contraseña
                     st.error("La contraseña debe tener al menos 6 caracteres.")
                elif new_username in user_data:
                    st.error("Este nombre de usuario (email) ya existe. Por favor, elige otro o inicia sesión.")
                else:
                    # Registrar nuevo usuario estudiante
                    hashed_pass = hash_password(new_password)
                    user_data[new_username] = {
                        "hashed_password_with_salt": hashed_pass,
                        "level": DEFAULT_LEVEL, # Nivel inicial por defecto
                        "is_admin": False       # Siempre False al registrarse por esta vía
                    }
                    save_user_data(user_data) # Guardar los datos actualizados
                    st.success(f"¡Usuario '{new_username}' registrado con éxito! Ahora puedes iniciar sesión.")
                    time.sleep(2)
                    # No redirigimos, dejamos que hagan clic en "Iniciar Sesión"

# --- Contenido Principal (Si está logueado) ---
else:
    # Mostrar usuario y botón de logout en la barra lateral
    st.sidebar.header(f"Usuario:")
    st.sidebar.write(st.session_state.username)
    if st.sidebar.button("Cerrar Sesión"):
        # Guardar nivel actual antes de salir (si no es admin)
        if not st.session_state.is_admin and st.session_state.username in user_data:
             current_level_to_save = st.session_state.current_level
             # Recargar datos por si cambiaron mientras estaba logueado
             user_data = load_user_data()
             if st.session_state.username in user_data: # Verificar que el usuario aún exista
                 user_data[st.session_state.username]['level'] = current_level_to_save
                 save_user_data(user_data)

        # Limpiar todo el session state relacionado al usuario y estado
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        # Reinicializar estado básico para la pantalla de login
        st.session_state.logged_in = False
        st.rerun()

    # --- Vista de Administrador ---
    if st.session_state.is_admin:
        st.title("Panel de Administración")
        st.write("Datos de los estudiantes registrados:")

        # Recargar datos frescos para la vista de admin
        user_data = load_user_data()

        student_data = []
        for user, data in user_data.items():
            if not data.get('is_admin', False): # Excluir otros posibles admins
                student_data.append({
                    "Usuario (Email)": user,
                    "Nivel Actual": data.get('level', 'N/A')
                    # IMPORTANTE: Nunca mostrar contraseñas, ni siquiera hasheadas.
                })

        if student_data:
            # Ordenar por usuario para consistencia
            df = pd.DataFrame(student_data).sort_values(by="Usuario (Email)").reset_index(drop=True)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Aún no hay estudiantes registrados.")

    # --- Vista de Estudiante ---
    else:
        st.title("🚀 Práctica de Comprensión Lectora 🚀")
        st.info(f"Tu nivel actual de práctica: **{st.session_state.current_level}** (de {MIN_LEVEL} a {MAX_LEVEL})")
        st.markdown("---")

        # --- Generación de Texto y Preguntas (si es necesario) ---
        if st.session_state.current_text is None or st.session_state.current_questions is None:
            if st.button("Comenzar Práctica / Siguiente Texto", key="start_next_initial", type="primary"):
                with st.spinner(f"Generando nuevo texto y preguntas para el nivel {st.session_state.current_level}... Esto puede tardar unos segundos."):
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
                            st.error("Error: No se pudieron generar las preguntas para este texto. Por favor, intenta generar un nuevo texto.")
                            # Limpiar texto para evitar inconsistencias
                            st.session_state.current_text = None
                            st.session_state.current_questions = None
                    else:
                        st.error("Error: No se pudo generar un nuevo texto. Por favor, inténtalo de nuevo.")
                        st.session_state.current_text = None
                        st.session_state.current_questions = None
            else:
                 st.info("Haz clic en el botón de arriba para empezar o continuar tu práctica.")


        # --- Mostrar Texto y Preguntas (si existen) ---
        elif st.session_state.current_text and st.session_state.current_questions:
            st.subheader("📖 Lee el siguiente texto:")
            # Usar st.text_area para mejor formato y posible scroll si el texto es largo
            st.text_area("Texto", st.session_state.current_text, height=200, disabled=True, label_visibility="collapsed")
            st.markdown("---")
            st.subheader("🤔 Responde las preguntas:")

            # Usar un formulario para agrupar las preguntas y el botón de envío
            with st.form("qa_form"):
                temp_answers = {} # Guardar temporalmente las respuestas seleccionadas en este ciclo
                for i, q in enumerate(st.session_state.current_questions):
                    options_list = list(q["options"].values()) # ["Texto op A", "Texto op B", ...]
                    options_dict = q["options"]             # {"A": "Texto op A", ...}
                    # Crear etiquetas únicas para cada radio button
                    radio_key = f"q_{i}_{st.session_state.current_level}_{len(st.session_state.current_text or '')}" # Key más única
                    
                    # Obtener la respuesta previamente seleccionada si ya se envió
                    current_selection_index = None
                    if st.session_state.submitted_answers and i in st.session_state.user_answers:
                         selected_letter = st.session_state.user_answers[i]
                         if selected_letter in options_dict:
                             selected_text = options_dict[selected_letter]
                             try:
                                 current_selection_index = options_list.index(selected_text)
                             except ValueError:
                                 current_selection_index = None # Si la opción guardada ya no existe?

                    selected_option_text = st.radio(
                        label=f"**{i+1}. {q['question']}**",
                        options=options_list,
                        key=radio_key,
                        index=current_selection_index, # Mantener selección después de enviar
                        # Deshabilitar si ya se enviaron las respuestas para este set
                        disabled=st.session_state.submitted_answers
                    )
                    
                    # Guardar la LETRA de la opción seleccionada (A, B, C, D)
                    selected_letter = None
                    if selected_option_text: # Si el usuario seleccionó algo
                        for letter, text in options_dict.items():
                            if text == selected_option_text:
                                selected_letter = letter
                                break
                    temp_answers[i] = selected_letter # Usamos índice como clave

                # Botón de envío dentro del formulario
                submit_button = st.form_submit_button("✔️ Enviar Respuestas", disabled=st.session_state.submitted_answers)

                if submit_button and not st.session_state.submitted_answers:
                    # Verificar que todas las preguntas fueron respondidas (opcional, pero recomendado)
                    if None in temp_answers.values():
                         st.warning("Por favor, responde todas las preguntas antes de enviar.")
                    else:
                        st.session_state.user_answers = temp_answers # Guardar respuestas definitivas
                        st.session_state.submitted_answers = True
                        st.session_state.feedback_given = False # Marcar para dar feedback ahora
                        st.rerun() # Re-ejecutar para mostrar feedback

            # --- Mostrar Feedback y Resultados (después de enviar) ---
            if st.session_state.submitted_answers:
                st.markdown("---")
                st.subheader("📊 Resultados de esta Ronda")

                correct_count = 0
                results_display = [] # Para mostrar feedback detallado

                for i, q in enumerate(st.session_state.current_questions):
                    user_ans_letter = st.session_state.user_answers.get(i) # Letra ('A', 'B', ...) o None
                    correct_ans_letter = q["correct_answer"] # Letra ('A', 'B', ...)
                    is_correct = (user_ans_letter == correct_ans_letter)
                    
                    if is_correct:
                        correct_count += 1

                    # Preparar feedback visual
                    result_text = f"**Pregunta {i+1}:** {q['question']}\n"
                    options_dict = q["options"]

                    if user_ans_letter and user_ans_letter in options_dict:
                         result_text += f"*   Tu respuesta: **{user_ans_letter}**. {options_dict[user_ans_letter]}"
                    elif user_ans_letter: # Respuesta inválida guardada?
                         result_text += f"*   Tu respuesta: {user_ans_letter} (Opción inválida)"
                    else:
                         result_text += f"*   No respondiste."

                    if is_correct:
                        result_text += " (Correcto ✔️)"
                    else:
                        correct_option_text = options_dict.get(correct_ans_letter, "[Opción correcta no encontrada]")
                        result_text += f" (Incorrecto ❌ - La correcta era: **{correct_ans_letter}**. {correct_option_text})"
                    results_display.append(result_text)

                st.session_state.score = correct_count

                # Mostrar score general
                st.metric(label="Respuestas Correctas", value=f"{st.session_state.score} de {len(st.session_state.current_questions)}")

                # Mostrar feedback detallado en un expander
                with st.expander("Ver detalle de respuestas"):
                    for result_item in results_display:
                         st.markdown(result_item)
                         st.markdown("---") # Separador entre preguntas

                # --- Lógica de Adaptación de Nivel (Solo una vez por ronda) ---
                if not st.session_state.feedback_given:
                    previous_level = st.session_state.current_level
                    level_changed = False

                    if st.session_state.score >= 4: # 80% o más -> Subir nivel
                        if st.session_state.current_level < MAX_LEVEL:
                            st.session_state.current_level += 1
                            st.success("¡Excelente trabajo! Aumentando un poco la dificultad para el siguiente texto.")
                            level_changed = True
                        else:
                            st.success("¡Excelente trabajo! Ya estás en el nivel máximo.")
                    elif st.session_state.score <= 1: # 20% o menos -> Bajar nivel
                        if st.session_state.current_level > MIN_LEVEL:
                            st.session_state.current_level -= 1
                            st.warning("Parece que este texto fue un desafío. Probemos uno un poco más sencillo.")
                            level_changed = True
                        else:
                             st.info("¡Sigue intentando! Ya estás en el nivel inicial.")
                    else: # 2 o 3 correctas -> Mantener nivel
                        st.info("¡Buen intento! Mantendremos este nivel de dificultad por ahora.")

                    if level_changed:
                         st.write(f"Tu nuevo nivel de práctica será: **{st.session_state.current_level}**")
                         # Guardar el nuevo nivel inmediatamente en el archivo JSON
                         # Recargar datos por si acaso, luego actualizar y guardar
                         user_data_update = load_user_data()
                         if st.session_state.username in user_data_update:
                             user_data_update[st.session_state.username]['level'] = st.session_state.current_level
                             save_user_data(user_data_update)
                         else:
                             st.error("Error al guardar el nivel: no se encontró el usuario. Contacta al administrador.")


                    st.session_state.feedback_given = True # Marcar que el feedback/ajuste ya se hizo para esta ronda

                # --- Botón para Siguiente Texto ---
                if st.button("➡️ Ir al Siguiente Texto", key="next_text_button"):
                    # Limpiar estado para generar nuevo contenido
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.user_answers = {}
                    st.session_state.submitted_answers = False
                    st.session_state.score = 0
                    st.session_state.feedback_given = False # Resetear para la próxima ronda
                    st.rerun() # Recargar para disparar la lógica de generación/botón inicial

# --- Footer o información adicional ---
st.markdown("---")
st.caption("Aplicación de práctica de lectura v1.1 - Desarrollada con Streamlit y Google Gemini")
