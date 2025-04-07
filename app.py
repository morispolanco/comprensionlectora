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
    # Usar un número de iteraciones más alto es más seguro, pero más lento. 100k es un buen punto de partida.
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ':' + pwd_hash.hex()

def verify_password(stored_password_with_salt, provided_password):
    """Verifica la contraseña proporcionada contra el hash almacenado."""
    try:
        salt_hex, stored_hash_hex = stored_password_with_salt.split(':')
        salt = bytes.fromhex(salt_hex)
        stored_hash = bytes.fromhex(stored_hash_hex)
        # Usa los mismos parámetros (salt, iteraciones) que al hashear
        pwd_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return pwd_hash == stored_hash
    except (ValueError, IndexError): # Si el split falla o falta el formato
        # Loguear el error internamente es mejor que mostrarlo al usuario
        print(f"Error interno: Formato de contraseña almacenada inválido: {stored_password_with_salt[:10]}...") # No mostrar toda la cadena
        return False
    except Exception as e:
        print(f"Error inesperado al verificar contraseña: {e}") # Loguear el error para el desarrollador
        return False

def load_user_data():
    """Carga los datos de usuario desde el archivo JSON."""
    # Asegurar que el archivo existe y crear uno vacío si no, o manejar la creación del admin
    if not os.path.exists(USER_DATA_FILE):
        st.warning(f"Archivo '{USER_DATA_FILE}' no encontrado. Creando uno nuevo con el usuario admin.")
        admin_user = "mp@ufm.edu" # Considera sacar esto a variables de entorno o secrets si es sensible
        admin_pass = "moris123"   # ¡Definitivamente no hardcodear contraseñas en producción! Usar secrets o input manual.
        hashed_admin_pass = hash_password(admin_pass)
        initial_data = {
            admin_user: {
                "hashed_password_with_salt": hashed_admin_pass,
                "level": None, # Los admins no tienen nivel
                "is_admin": True
            }
        }
        save_user_data(initial_data)
        return initial_data

    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f: # Especificar encoding
            content = f.read()
            if not content.strip(): # Usar strip() para manejar archivos con solo espacios en blanco
                 st.error(f"Error: El archivo '{USER_DATA_FILE}' está vacío. Eliminando y regenerando con admin.")
                 # Podríamos borrar el archivo y llamar a load_user_data() de nuevo para recrearlo.
                 try:
                     os.remove(USER_DATA_FILE)
                     return load_user_data() # Llamada recursiva para recrear
                 except OSError as e:
                     st.error(f"No se pudo eliminar el archivo vacío '{USER_DATA_FILE}': {e}. Se necesita intervención manual.")
                     return {}
            return json.loads(content)
    except json.JSONDecodeError:
        st.error(f"Error: El archivo '{USER_DATA_FILE}' está corrupto o no es JSON válido. Renómbrelo o bórrelo para regenerar al usuario admin.")
        # Podrías intentar renombrar el archivo corrupto aquí
        # Ejemplo: os.rename(USER_DATA_FILE, USER_DATA_FILE + f".corrupt_{int(time.time())}")
        return {} # Devuelve vacío para evitar más errores
    except Exception as e:
        st.error(f"Error inesperado al cargar datos de usuario: {e}")
        return {}

def save_user_data(data):
    """Guarda los datos de usuario en el archivo JSON."""
    try:
        # Escritura atómica (más segura contra corrupción si el proceso falla a mitad):
        temp_file = USER_DATA_FILE + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f: # Especificar encoding
            json.dump(data, f, indent=4, ensure_ascii=False) # ensure_ascii=False si hay caracteres no latinos
        os.replace(temp_file, USER_DATA_FILE) # Renombrar el temporal al archivo final (atómico en la mayoría de SO)
    except Exception as e:
        st.error(f"Error crítico al guardar datos de usuario: {e}")
        # Intentar eliminar el archivo temporal si existe
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass # No mucho más que hacer aquí

# --- Configuración de Gemini ---
# Usar un bloque try-except para la configuración inicial es bueno
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
    # Considerar hacer el modelo configurable si se quiere probar con otros
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash', # Usar el modelo flash es bueno para velocidad/costo
        safety_settings=safety_settings,
        # Opcional: generation_config para controlar temperatura, top_p, etc.
        # generation_config=genai.types.GenerationConfig(temperature=0.7)
        )
    # print("Gemini Model Initialized") # Debugging
except KeyError:
    st.error("Error Crítico: No se encontró la clave 'GEMINI_API_KEY' en los secrets de Streamlit (archivo .streamlit/secrets.toml). La aplicación no puede funcionar sin ella.")
    st.stop() # Detiene la ejecución si no hay API key
except Exception as e:
    st.error(f"Error crítico al configurar la API de Google Gemini: {e}")
    st.stop()

# --- Funciones de Generación con Gemini ---

# Variable global para contar reintentos de generación (opcional, para depuración)
# gemini_retry_count = 0

def generate_reading_text(level):
    """Genera un texto de lectura adaptado al nivel con reintentos."""
    global gemini_retry_count
    if level <= 2:
        difficulty_desc = "muy fácil, con vocabulario simple (A1-A2 CEFR) y frases cortas y directas"
        words = "50-80"
        topic_suggestion = "una descripción simple de un animal, un objeto cotidiano o una acción simple."
    elif level <= 4:
        difficulty_desc = "fácil (A2-B1 CEFR), con vocabulario común y frases relativamente cortas, quizás con alguna conjunción simple"
        words = "80-120"
        topic_suggestion = "una anécdota breve, una descripción de un lugar conocido, o una explicación simple de un hobby."
    elif level <= 6:
        difficulty_desc = "intermedio (B1 CEFR), con algo de vocabulario variado, frases de longitud media, y quizás tiempos verbales pasados simples"
        words = "120-180"
        topic_suggestion = "un resumen de una noticia sencilla, una descripción de un proceso simple, o una opinión breve sobre un tema general."
    elif level <= 8:
        difficulty_desc = "intermedio-alto (B2 CEFR), con vocabulario más rico, estructuras de frases más complejas (subordinadas), y uso variado de tiempos verbales"
        words = "180-250"
        topic_suggestion = "una explicación de un concepto científico básico, un relato histórico corto, o una reseña simple de un libro o película."
    else: # level 9-10
        difficulty_desc = "avanzado (C1 CEFR), con vocabulario avanzado, expresiones idiomáticas (pocas), y frases largas y complejas"
        words = "250-350"
        topic_suggestion = "un análisis corto de un tema social, una reflexión sobre una obra de arte, o una introducción a una tecnología emergente."

    # Prompt mejorado con más contexto y restricciones
    prompt = f"""
    Eres un asistente experto en crear material didáctico de español como lengua extranjera (ELE) o para nativos jóvenes.
    Tu tarea es generar un texto de lectura en ESPAÑOL para un estudiante de Quinto Bachillerato (aproximadamente 16-17 años).
    El nivel de dificultad requerido es {level} (en una escala de {MIN_LEVEL} a {MAX_LEVEL}), que corresponde a un nivel {difficulty_desc}.
    El tema debe ser interesante, educativo y apropiado para adolescentes (ej: ciencia divulgativa, historia breve, tecnología actual, arte, sociedad, naturaleza, cultura general). Sugerencia para este nivel: {topic_suggestion}
    El texto debe tener una longitud aproximada de {words} palabras.
    El texto debe ser coherente, autocontenido y permitir formular 5 preguntas claras de comprensión lectora sobre él (idea principal, detalles, inferencia simple, vocabulario en contexto). No puede ser trivial.
    FORMATO DE SALIDA: Solo el texto de lectura. NO incluyas título, ni las preguntas, ni encabezados como "Texto:", ni notas adicionales. Solo el párrafo o párrafos.
    SEGURIDAD: Asegúrate de que el contenido sea completamente seguro, ético y apropiado para menores de edad (G-rated). Evita temas sensibles o controversiales de forma explícita.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            # print(f"Gemini Text Response (Attempt {attempt+1}): {response.text}") # Debugging
            # Validación más robusta de la respuesta
            if response.parts: # Verificar si hay partes en la respuesta
                 generated_text = response.text.strip()
                 # Verificar longitud mínima y que no sea solo texto genérico de error/rechazo
                 if len(generated_text) > 40 and "no puedo generar" not in generated_text.lower() and "contenido inapropiado" not in generated_text.lower():
                    # gemini_retry_count = 0 # Resetear contador en éxito
                    return generated_text
                 else:
                     print(f"Intento {attempt+1}: Texto generado demasiado corto o parece un rechazo. Longitud: {len(generated_text)}")
            else:
                 # Si no hay parts, puede ser por bloqueo de seguridad u otro problema
                 print(f"Intento {attempt+1}: La respuesta de Gemini no contiene partes de texto. Revisar prompt feedback si existe.")
                 try:
                    print(f"Prompt Feedback: {response.prompt_feedback}")
                    # Puedes añadir lógica aquí si el feedback indica un bloqueo específico
                 except Exception:
                     print("No se pudo obtener prompt feedback.")

            # Esperar antes de reintentar solo si no es el último intento
            if attempt < max_retries - 1:
                # gemini_retry_count += 1
                print(f"Reintentando generación de texto (intento {attempt + 2}/{max_retries})...")
                time.sleep(1.5 ** attempt) # Backoff exponencial simple
            else:
                 st.error(f"Error: No se pudo generar un texto válido con Gemini después de {max_retries} intentos.")
                 # Mostrar el último texto (si hubo) para depuración
                 st.text_area("Último intento de texto (para depuración):", generated_text if 'generated_text' in locals() else "No text generated", height=100)
                 return None

        except Exception as e:
            st.error(f"Error en la llamada a la API de Gemini para generar texto (Intento {attempt+1}): {e}")
            # Mostrar feedback si está disponible en la excepción o la respuesta
            # try:
            #     st.error(f"Prompt Feedback: {response.prompt_feedback}")
            # except Exception: pass
            if attempt < max_retries - 1:
                 time.sleep(1.5 ** attempt)
            else:
                 st.error(f"Fallo definitivo al generar texto después de {max_retries} intentos.")
                 return None
    return None # Asegurar que siempre devuelva algo (None en caso de fallo total)

def generate_mc_questions(text):
    """Genera 5 preguntas de opción múltiple basadas en el texto, con validación JSON robusta y reintentos."""
    # global gemini_retry_count
    prompt = f"""
    Basado ESTRICTAMENTE en el siguiente texto en español, crea exactamente 5 preguntas de opción múltiple (A, B, C, D) para evaluar la comprensión lectora de un estudiante de Quinto Bachillerato.

    Requisitos INDISPENSABLES:
    1.  **Número Exacto:** Deben ser EXACTAMENTE 5 preguntas. Ni más, ni menos.
    2.  **Cobertura:** Las preguntas deben cubrir diferentes aspectos del texto (idea principal, detalles específicos clave, inferencias DIRECTAS Y OBVIAS soportadas por el texto, vocabulario relevante en contexto). No inventes información que no esté en el texto.
    3.  **Claridad:** Preguntas claras, concisas y sin ambigüedades.
    4.  **Opciones:**
        *   Exactamente CUATRO opciones por pregunta (A, B, C, D).
        *   Solo UNA opción debe ser la correcta según el texto.
        *   Las opciones incorrectas (distractores) deben ser plausibles (relacionadas al tema) pero CLARAMENTE incorrectas según la información explícita o implícita directa del texto. No deben ser trivialmente falsas ni requerir conocimiento externo.
    5.  **Idioma:** Todo en español (preguntas y opciones).
    6.  **Formato JSON Estricto:** La salida DEBE ser una lista JSON válida. Cada elemento de la lista será un objeto JSON con TRES claves OBLIGATORIAS y EXACTAS:
        *   `question`: (string) El texto completo de la pregunta.
        *   `options`: (objeto JSON) Un diccionario con exactamente cuatro pares clave-valor. Las claves DEBEN ser las letras MAYÚSCULAS "A", "B", "C", "D". Los valores deben ser los textos (string) de cada opción.
        *   `correct_answer`: (string) La letra MAYÚSCULA de la opción correcta (ej. "A", "B", "C", o "D"). Esta letra DEBE coincidir con una de las claves en el objeto `options`.

    Texto proporcionado:
    ---
    {text}
    ---

    IMPORTANTE: Responde ÚNICAMENTE con la lista JSON. No incluyas NINGÚN texto introductorio, NINGUNA explicación, ni uses bloques de código markdown (```json ... ```). Solo la lista JSON pura empezando con `[` y terminando con `]`.

    Ejemplo de formato de un elemento de la lista:
    {{
      "question": "¿Cuál es el tema principal del texto?",
      "options": {{
        "A": "Opción A",
        "B": "Opción B",
        "C": "Opción C",
        "D": "Opción D"
      }},
      "correct_answer": "B"
    }}
    """
    max_retries = 3
    last_error = ""
    raw_response_text = ""

    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            raw_response_text = response.text # Guardar siempre la última respuesta cruda

            # print(f"Gemini Questions Raw Response (Attempt {attempt+1}):\n{raw_response_text}") # Debugging

            # 1. Limpieza básica inicial
            # A veces Gemini puede añadir comillas extras al inicio/fin o markdown
            json_response_text = raw_response_text.strip()
            if json_response_text.startswith("```json"):
                json_response_text = json_response_text[7:]
            if json_response_text.endswith("```"):
                json_response_text = json_response_text[:-3]
            json_response_text = json_response_text.strip()

            # 2. Intentar parsear JSON
            try:
                parsed_data = json.loads(json_response_text)
            except json.JSONDecodeError as e:
                last_error = f"Intento {attempt+1}: JSONDecodeError - {e}. Respuesta recibida:\n{raw_response_text}"
                print(last_error)
                # No continuar si el JSON es inválido, reintentar
                if attempt < max_retries - 1:
                    # gemini_retry_count += 1
                    print(f"Reintentando generación de preguntas (intento {attempt + 2}/{max_retries})...")
                    time.sleep(1.5 ** attempt) # Backoff
                    continue
                else:
                    break # Salir del bucle si se agotaron los reintentos

            # 3. Validación de estructura y contenido
            validation_passed = False
            if isinstance(parsed_data, list) and len(parsed_data) == 5:
                valid_structure = True
                for i, q in enumerate(parsed_data):
                    # Verificar que cada elemento es un diccionario
                    if not isinstance(q, dict):
                        last_error = f"Error validación (Pregunta {i+1}): Elemento no es un diccionario."; valid_structure = False; break
                    # Verificar claves principales
                    if not all(k in q for k in ["question", "options", "correct_answer"]):
                        last_error = f"Error validación (Pregunta {i+1}): Faltan claves principales (question, options, correct_answer). Claves presentes: {list(q.keys())}"; valid_structure = False; break
                    # Verificar tipo de 'question' y 'correct_answer'
                    if not isinstance(q["question"], str) or not q["question"]:
                         last_error = f"Error validación (Pregunta {i+1}): 'question' debe ser un string no vacío."; valid_structure = False; break
                    if not isinstance(q["correct_answer"], str) or q["correct_answer"] not in ["A", "B", "C", "D"]:
                        last_error = f"Error validación (Pregunta {i+1}): 'correct_answer' debe ser 'A', 'B', 'C', o 'D'. Recibido: '{q['correct_answer']}'"; valid_structure = False; break
                    # Verificar 'options'
                    if not isinstance(q["options"], dict):
                        last_error = f"Error validación (Pregunta {i+1}): 'options' debe ser un diccionario."; valid_structure = False; break
                    if len(q["options"]) != 4 or not all(k in q["options"] for k in ["A", "B", "C", "D"]):
                         last_error = f"Error validación (Pregunta {i+1}): 'options' debe tener exactamente las claves 'A', 'B', 'C', 'D'. Claves presentes: {list(q['options'].keys())}"; valid_structure = False; break
                    # Verificar que todas las opciones sean strings no vacíos
                    for opt_key, opt_val in q["options"].items():
                         if not isinstance(opt_val, str) or not opt_val:
                             last_error = f"Error validación (Pregunta {i+1}, Opción {opt_key}): El texto de la opción no puede estar vacío."; valid_structure = False; break
                    if not valid_structure: break # Salir del bucle de preguntas si una falla
                    # Verificar que la respuesta correcta exista como opción
                    if q["correct_answer"] not in q["options"]:
                        last_error = f"Error validación (Pregunta {i+1}): La 'correct_answer' ('{q['correct_answer']}') no existe como clave en 'options'."; valid_structure = False; break

                if valid_structure:
                    validation_passed = True
                    # gemini_retry_count = 0 # Resetear contador en éxito
                    # print(f"Parsed and Validated Questions: {parsed_data}") # Debugging
                    return parsed_data # Éxito, devolver las preguntas validadas

            else: # No es una lista o no tiene 5 elementos
                last_error = f"Intento {attempt+1}: La respuesta parseada no es una lista de 5 elementos. Tipo: {type(parsed_data)}, Longitud: {len(parsed_data) if isinstance(parsed_data, list) else 'N/A'}"
                print(last_error)

            # Si la validación falló y quedan reintentos
            if not validation_passed and attempt < max_retries - 1:
                # gemini_retry_count += 1
                print(f"Reintentando generación de preguntas debido a error de validación (intento {attempt + 2}/{max_retries})...")
                time.sleep(1.5 ** attempt) # Backoff

        except Exception as e:
            last_error = f"Error crítico inesperado al generar/procesar preguntas (Intento {attempt+1}): {e}"
            st.error(last_error) # Mostrar error crítico inmediatamente
            # Mostrar feedback si está disponible
            # try:
            #     st.error(f"Prompt Feedback: {response.prompt_feedback}")
            # except Exception: pass
            if attempt < max_retries - 1:
                 time.sleep(1.5 ** attempt)
            else:
                 st.error(f"Fallo definitivo al generar preguntas después de {max_retries} intentos.")
                 # Mostrar la última respuesta cruda para depuración en caso de fallo total
                 st.text_area("Última respuesta cruda recibida (para depuración):", raw_response_text if raw_response_text else "No response text available", height=150)
                 return None

    # Si el bucle termina sin éxito
    st.error(f"Error: Gemini no devolvió 5 preguntas en el formato JSON esperado y validado después de {max_retries} intentos.")
    st.warning(f"Último error registrado: {last_error}")
    st.text_area("Última respuesta cruda recibida (para depuración):", raw_response_text if raw_response_text else "No response text available", height=150)
    return None

# --- Información en la Barra Lateral (Siempre visible) ---
st.sidebar.title("📖 Práctica Lectora Adaptativa")
st.sidebar.markdown("""
Esta aplicación utiliza Inteligencia Artificial (**Google Gemini 1.5 Flash**) para generar textos y preguntas adaptados a tu nivel de comprensión lectora.

**¿Cómo funciona?**
1.  **Regístrate** o inicia sesión.
2.  La app te asignará un **nivel inicial** (o usará el último guardado).
3.  Pulsa "Comenzar Práctica" para obtener un **texto** según tu nivel.
4.  Lee el texto y responde las **preguntas** de opción múltiple.
5.  Al enviar, verás tu **puntuación**.
6.  La aplicación **ajustará la dificultad** (tu nivel) para el siguiente texto según tus resultados.
¡Practica y mejora a tu propio ritmo!
""")
st.sidebar.divider() # Separador visual
st.sidebar.subheader("Desarrollador")
st.sidebar.info("Moris Polanco")
st.sidebar.write("📧 mp@ufm.edu")
# Usamos markdown para crear un enlace clickeable
st.sidebar.markdown("🌐 [morispolanco.vercel.app](https://morispolanco.vercel.app)")
st.sidebar.divider()

# --- Inicialización del Estado de la Sesión ---
# Es crucial para mantener el estado entre interacciones del usuario en Streamlit
default_session_state = {
    'logged_in': False,
    'username': None,
    'is_admin': False,
    'current_level': DEFAULT_LEVEL,
    'current_text': None,
    'current_questions': None,
    'user_answers': {}, # Almacenará {question_index: selected_letter}
    'submitted_answers': False, # Flag para saber si ya se envió la ronda actual
    'score': 0,
    'feedback_given': False # Flag para controlar que el ajuste de nivel se haga solo una vez por ronda
}
# Inicializar solo las claves que no existan
for key, value in default_session_state.items():
    if key not in st.session_state:
        st.session_state[key] = value

# --- Lógica de Autenticación y Registro ---
user_data = load_user_data() # Cargar datos al inicio

# Solo mostrar Login/Registro si el usuario NO está logueado
if not st.session_state.logged_in:
    st.title("Bienvenido/a a la Práctica de Comprensión Lectora")

    auth_choice = st.radio("Selecciona una opción:", ("Iniciar Sesión", "Registrarse"), horizontal=True, key="auth_choice")

    if auth_choice == "Iniciar Sesión":
        st.subheader("Iniciar Sesión")
        # Usar un formulario previene que la página se recargue con cada tecla presionada en los inputs
        with st.form("login_form"):
            username = st.text_input("Usuario (Email)", key="login_user").lower().strip() # Normalizar email
            password = st.text_input("Contraseña", type="password", key="login_pass")
            submitted = st.form_submit_button("Entrar")

            if submitted:
                if not username or not password:
                    st.warning("Por favor, ingresa usuario y contraseña.")
                # Verificar primero si el usuario existe
                elif username in user_data:
                    user_info = user_data[username]
                    stored_pass_info = user_info.get('hashed_password_with_salt')
                    # Verificar que la contraseña almacenada existe y es válida
                    if stored_pass_info and verify_password(stored_pass_info, password):
                        # Éxito en la autenticación
                        st.session_state.logged_in = True
                        st.session_state.username = username
                        st.session_state.is_admin = user_info.get('is_admin', False)
                        # Cargar nivel solo si NO es admin
                        if not st.session_state.is_admin:
                            st.session_state.current_level = user_info.get('level', DEFAULT_LEVEL)
                        else:
                             st.session_state.current_level = None # Admin no tiene nivel asociado

                        # Limpiar estado de práctica anterior al iniciar sesión
                        st.session_state.current_text = None
                        st.session_state.current_questions = None
                        st.session_state.user_answers = {}
                        st.session_state.submitted_answers = False
                        st.session_state.score = 0
                        st.session_state.feedback_given = False

                        st.success(f"¡Bienvenido/a {username}!")
                        time.sleep(1.5) # Pausa para que el usuario vea el mensaje
                        st.rerun() # Recarga la app para mostrar el contenido principal
                    else:
                        # Contraseña incorrecta
                        st.error("Usuario o contraseña incorrectos.")
                else:
                    # Usuario no encontrado
                    st.error("Usuario o contraseña incorrectos.")

    elif auth_choice == "Registrarse":
        st.subheader("Registrar Nuevo Usuario (Estudiante)")
        with st.form("register_form"):
            new_username = st.text_input("Nuevo Usuario (Email)", key="reg_user").lower().strip() # Normalizar email
            new_password = st.text_input("Nueva Contraseña", type="password", key="reg_pass")
            confirm_password = st.text_input("Confirmar Contraseña", type="password", key="reg_confirm")
            submitted = st.form_submit_button("Registrar")

            if submitted:
                # Validaciones exhaustivas antes de registrar
                error_found = False
                if not new_username or not new_password or not confirm_password:
                    st.warning("Por favor, completa todos los campos.")
                    error_found = True
                if new_password != confirm_password:
                    st.error("Las contraseñas no coinciden.")
                    error_found = True
                # Validación simple de formato de email
                if "@" not in new_username or "." not in new_username.split('@')[-1]:
                     st.error("Por favor, usa un formato de email válido para el usuario (ej: nombre@dominio.com).")
                     error_found = True
                # Validación simple de longitud de contraseña
                if len(new_password) < 6:
                     st.error("La contraseña debe tener al menos 6 caracteres.")
                     error_found = True
                # Verificar si el usuario ya existe (después de cargar datos)
                if new_username in user_data:
                    st.error("Este nombre de usuario (email) ya está registrado. Por favor, elige otro o inicia sesión.")
                    error_found = True

                if not error_found:
                    # Si todas las validaciones pasan, registrar al usuario
                    hashed_pass = hash_password(new_password)
                    user_data[new_username] = {
                        "hashed_password_with_salt": hashed_pass,
                        "level": DEFAULT_LEVEL, # Nivel inicial por defecto para nuevos estudiantes
                        "is_admin": False       # Los usuarios registrados por esta vía nunca son admin
                    }
                    save_user_data(user_data) # Guardar los datos actualizados en el archivo
                    st.success(f"¡Usuario '{new_username}' registrado con éxito! Ahora puedes ir a 'Iniciar Sesión'.")
                    time.sleep(2.5) # Pausa más larga para leer el mensaje
                    # No se hace rerun aquí, el usuario debe cambiar a "Iniciar Sesión" manualmente

# --- Contenido Principal (Si está logueado) ---
else:
    # Mostrar información del usuario y botón de logout en la barra lateral
    st.sidebar.header(f"Usuario:")
    st.sidebar.write(st.session_state.username)
    if st.sidebar.button("Cerrar Sesión", key="logout_button"):
        # Antes de cerrar sesión, guardar el nivel actual del estudiante
        if not st.session_state.is_admin and st.session_state.username:
             try:
                 # Recargar datos frescos por si hubo cambios externos (poco probable aquí, pero buena práctica)
                 user_data_logout = load_user_data()
                 if st.session_state.username in user_data_logout: # Verificar que el usuario aún exista
                     user_data_logout[st.session_state.username]['level'] = st.session_state.current_level
                     save_user_data(user_data_logout)
                 else:
                      print(f"Advertencia: Usuario {st.session_state.username} no encontrado al intentar guardar nivel en logout.")
             except Exception as e:
                  print(f"Error al guardar nivel del usuario {st.session_state.username} en logout: {e}")

        # Limpiar todas las claves relevantes del estado de sesión al cerrar sesión
        keys_to_clear = list(default_session_state.keys()) # Usar las claves por defecto como referencia
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]

        # Forzar el estado a no logueado y recargar para volver a la pantalla de inicio/registro
        st.session_state.logged_in = False # Asegurar que esté en False
        st.rerun()

    # --- Vista de Administrador ---
    if st.session_state.is_admin:
        st.title("Panel de Administración")
        st.write("Vista de los estudiantes registrados y sus niveles:")

        # Recargar datos frescos siempre que se acceda a la vista de admin
        user_data_admin = load_user_data()

        student_data_list = []
        for user, data in user_data_admin.items():
            # Filtrar para mostrar solo estudiantes (no admins)
            if not data.get('is_admin', False):
                student_data_list.append({
                    "Usuario (Email)": user,
                    "Nivel Actual": data.get('level', 'N/A') # Mostrar N/A si falta el nivel
                    # Nunca mostrar contraseñas o hashes aquí
                })

        if student_data_list:
            # Convertir a DataFrame de Pandas para una tabla bonita
            df_students = pd.DataFrame(student_data_list)
            # Ordenar por email para consistencia
            df_students = df_students.sort_values(by="Usuario (Email)").reset_index(drop=True)
            # Mostrar la tabla usando todo el ancho disponible
            st.dataframe(df_students, use_container_width=True)
        else:
            st.info("Aún no hay estudiantes registrados en el sistema.")

        # Opcional: Añadir funcionalidad de admin (ej: borrar usuario, cambiar nivel) - ¡CON PRECAUCIÓN!
        # st.subheader("Acciones de Administrador")
        # selected_user = st.selectbox("Seleccionar usuario para modificar:", options=[s["Usuario (Email)"] for s in student_data_list])
        # new_level = st.number_input("Nuevo nivel:", min_value=MIN_LEVEL, max_value=MAX_LEVEL, value=user_data_admin[selected_user]['level'])
        # if st.button("Actualizar Nivel"): ... (implementar lógica de actualización y guardado)
        # if st.button("Eliminar Usuario", type="secondary"): ... (implementar lógica de eliminación con confirmación)

    # --- Vista de Estudiante ---
    else:
        st.title("🚀 Práctica de Comprensión Lectora 🚀")
        st.info(f"Tu nivel actual de práctica: **{st.session_state.current_level}** (Escala: {MIN_LEVEL} a {MAX_LEVEL})")
        st.markdown("---") # Separador visual

        # --- Lógica de Generación / Inicio de Ronda ---
        # Si no hay texto o preguntas cargadas, mostrar el botón para iniciar/continuar
        if st.session_state.current_text is None or st.session_state.current_questions is None:
            # El botón cambia su texto dependiendo de si es la primera vez o se viene de una ronda anterior
            button_text = "Comenzar Práctica" if st.session_state.score == 0 else "Siguiente Texto"
            if st.button(button_text, key="start_next_button", type="primary", use_container_width=True):
                level_to_generate = st.session_state.current_level
                with st.spinner(f"Generando nuevo texto y preguntas para el nivel {level_to_generate}... Esto puede tardar unos segundos..."):
                    # print(f"Requesting text generation for level {level_to_generate}") # Debugging
                    new_text = generate_reading_text(level_to_generate)

                    if new_text:
                        # print(f"Text generated successfully (length: {len(new_text)}), requesting questions...") # Debugging
                        new_questions = generate_mc_questions(new_text)

                        if new_questions:
                            # print("Questions generated and validated successfully.") # Debugging
                            # Actualizar el estado de la sesión con el nuevo contenido
                            st.session_state.current_text = new_text
                            st.session_state.current_questions = new_questions
                            st.session_state.user_answers = {} # Limpiar respuestas anteriores
                            st.session_state.submitted_answers = False # Resetear flags para la nueva ronda
                            st.session_state.score = 0
                            st.session_state.feedback_given = False
                            st.rerun() # Recargar para mostrar el nuevo texto y preguntas
                        else:
                            # Error al generar preguntas
                            st.error("Lo sentimos, hubo un problema al generar las preguntas para este texto. Por favor, intenta generar un nuevo texto.")
                            # Limpiar texto para forzar regeneración completa
                            st.session_state.current_text = None
                            st.session_state.current_questions = None
                    else:
                        # Error al generar texto
                        st.error("Lo sentimos, hubo un problema al generar el texto. Por favor, inténtalo de nuevo.")
                        st.session_state.current_text = None
                        st.session_state.current_questions = None
            else:
                # Mensaje inicial o entre rondas antes de pulsar el botón
                 if not st.session_state.submitted_answers: # No mostrar si acaba de terminar una ronda
                     st.info("Haz clic en el botón de arriba para empezar o continuar tu práctica.")

        # --- Mostrar Texto y Preguntas (si ya están generados) ---
        elif st.session_state.current_text and st.session_state.current_questions:
            st.subheader("📖 Lee el siguiente texto:")
            # Usar st.markdown para mejor formato visual del texto, o text_area si se prefiere scroll
            st.markdown(f"<div style='background-color:#f0f2f6; padding: 15px; border-radius: 10px; border: 1px solid #dadee3;'>{st.session_state.current_text}</div>", unsafe_allow_html=True)
            # Alternativa con text_area:
            # st.text_area("Texto:", st.session_state.current_text, height=200, disabled=True, label_visibility="collapsed")

            st.markdown("---")
            st.subheader("🤔 Responde las preguntas:")

            # Usar un formulario para agrupar las preguntas y el botón de envío
            # Esto evita que la app se recargue cada vez que se selecciona una opción de radio
            with st.form("qa_form"):
                temp_answers = {} # Diccionario temporal para recoger las selecciones del formulario
                questions_data = st.session_state.current_questions

                for i, q_data in enumerate(questions_data):
                    question_text = q_data['question']
                    options_dict = q_data['options'] # {"A": "Texto A", "B": "Texto B", ...}
                    options_list = [f"{letter}. {text}" for letter, text in options_dict.items()] # ["A. Texto A", "B. Texto B", ...]

                    # Clave única para el widget st.radio. Incluir algo del texto o hash puede ayudar a la unicidad si el texto cambia mucho
                    # Usar el índice y el nivel suele ser suficiente si las preguntas no se reordenan
                    radio_key = f"q_{i}_level_{st.session_state.current_level}"

                    # Si las respuestas ya fueron enviadas, encontrar el índice de la opción seleccionada previamente
                    # para mantenerla visible pero deshabilitada.
                    current_selection_index = None
                    if st.session_state.submitted_answers and i in st.session_state.user_answers:
                        selected_letter = st.session_state.user_answers[i]
                        option_texts = list(options_dict.values())
                        if selected_letter in options_dict:
                            try:
                                # Encontrar el índice en la lista formateada ["A. Texto A", ...]
                                target_text = f"{selected_letter}. {options_dict[selected_letter]}"
                                current_selection_index = options_list.index(target_text)
                            except ValueError:
                                pass # La opción seleccionada no se encontró (raro)

                    selected_option_formatted = st.radio(
                        label=f"**{i+1}. {question_text}**",
                        options=options_list, # Mostrar "A. Texto..."
                        key=radio_key,
                        index=current_selection_index, # Mantener selección si ya se envió/deshabilitó
                        disabled=st.session_state.submitted_answers # Deshabilitar después de enviar
                    )

                    # Extraer la letra (A, B, C, D) de la opción seleccionada ("A. Texto...")
                    selected_letter = None
                    if selected_option_formatted:
                        selected_letter = selected_option_formatted.split('.', 1)[0] # Obtiene la letra antes del primer punto

                    temp_answers[i] = selected_letter # Guardar la letra seleccionada (o None si no se selecciona nada)

                # Botón de envío dentro del formulario
                submit_button = st.form_submit_button(
                    "✔️ Enviar Respuestas",
                    disabled=st.session_state.submitted_answers, # Deshabilitar si ya se envió
                    use_container_width=True
                    )

                # --- Procesamiento al Enviar ---
                if submit_button and not st.session_state.submitted_answers:
                    # Verificar que todas las preguntas fueron respondidas
                    answered_all = all(ans is not None for ans in temp_answers.values())

                    if not answered_all:
                         st.warning("Por favor, responde todas las preguntas antes de enviar.")
                    else:
                        # Guardar las respuestas del usuario en el estado de sesión
                        st.session_state.user_answers = temp_answers
                        st.session_state.submitted_answers = True # Marcar como enviado
                        st.session_state.feedback_given = False # Resetear para permitir feedback/ajuste
                        st.rerun() # Re-ejecutar para mostrar la sección de resultados/feedback

            # --- Mostrar Feedback y Resultados (después de enviar) ---
            if st.session_state.submitted_answers:
                st.markdown("---")
                st.subheader("📊 Resultados de esta Ronda")

                correct_count = 0
                results_feedback = [] # Lista para almacenar el texto de feedback por pregunta

                questions_data = st.session_state.current_questions
                user_answers = st.session_state.user_answers

                for i, q_data in enumerate(questions_data):
                    user_ans_letter = user_answers.get(i) # Letra 'A', 'B', ... o None
                    correct_ans_letter = q_data["correct_answer"] # Letra 'A', 'B', ...
                    options_dict = q_data["options"]

                    is_correct = (user_ans_letter == correct_ans_letter)
                    if is_correct:
                        correct_count += 1

                    # Construir el texto de feedback para esta pregunta
                    feedback_item = f"**Pregunta {i+1}:** {q_data['question']}\n"
                    user_choice_text = options_dict.get(user_ans_letter, "*No respondida*")
                    correct_choice_text = options_dict.get(correct_ans_letter, "*Respuesta correcta no definida*")

                    if is_correct:
                        feedback_item += f"*   ✔️ Tu respuesta: **{user_ans_letter}.** {user_choice_text} (Correcto)"
                    else:
                        feedback_item += f"*   ❌ Tu respuesta: **{user_ans_letter}.** {user_choice_text} (Incorrecto)\n"
                        feedback_item += f"*   Respuesta correcta: **{correct_ans_letter}.** {correct_choice_text}"

                    results_feedback.append(feedback_item)

                # Guardar la puntuación en el estado de sesión
                st.session_state.score = correct_count
                num_questions = len(questions_data)

                # Mostrar puntuación general de forma destacada
                st.metric(label="Puntuación de esta ronda", value=f"{st.session_state.score} / {num_questions}")

                # Mostrar feedback detallado en un expander para no saturar la vista
                with st.expander("Ver detalle de respuestas", expanded=True): # Empezar expandido
                    for feedback_text in results_feedback:
                         st.markdown(feedback_text)
                         st.markdown("---") # Separador entre el feedback de cada pregunta

                # --- Lógica de Adaptación de Nivel ---
                # Ejecutar solo UNA VEZ por ronda, después de mostrar los resultados
                if not st.session_state.feedback_given:
                    previous_level = st.session_state.current_level
                    level_changed = False
                    feedback_message = ""

                    # Definir umbrales para subir/bajar/mantener nivel
                    # Umbrales: >=80% sube, <=40% baja, el resto mantiene (ajustable)
                    score_percentage = (st.session_state.score / num_questions) * 100

                    if score_percentage >= 80: # Subir nivel
                        if st.session_state.current_level < MAX_LEVEL:
                            st.session_state.current_level += 1
                            feedback_message = f"¡Excelente! ({score_percentage:.0f}%). Has subido al nivel **{st.session_state.current_level}**."
                            st.success(feedback_message)
                            level_changed = True
                        else:
                            feedback_message = f"¡Excelente trabajo! ({score_percentage:.0f}%). Ya estás en el nivel máximo ({MAX_LEVEL})."
                            st.success(feedback_message)
                    elif score_percentage <= 40: # Bajar nivel
                        if st.session_state.current_level > MIN_LEVEL:
                            st.session_state.current_level -= 1
                            feedback_message = f"({score_percentage:.0f}%) Parece que necesitas un poco más de práctica en el nivel anterior. Has bajado al nivel **{st.session_state.current_level}**."
                            st.warning(feedback_message)
                            level_changed = True
                        else:
                             feedback_message = f"({score_percentage:.0f}%) ¡Sigue practicando! Estás en el nivel inicial ({MIN_LEVEL})."
                             st.info(feedback_message)
                    else: # Mantener nivel (entre 41% y 79%)
                        feedback_message = f"¡Buen esfuerzo! ({score_percentage:.0f}%). Mantendremos el nivel **{st.session_state.current_level}** para la siguiente ronda."
                        st.info(feedback_message)

                    # Si el nivel cambió, guardarlo inmediatamente en el archivo JSON
                    if level_changed:
                         try:
                             user_data_update = load_user_data() # Recargar datos
                             if st.session_state.username in user_data_update:
                                 user_data_update[st.session_state.username]['level'] = st.session_state.current_level
                                 save_user_data(user_data_update)
                                 # print(f"Nivel del usuario {st.session_state.username} actualizado a {st.session_state.current_level} y guardado.") # Debugging
                             else:
                                 st.error("Error crítico: No se encontró tu usuario para guardar el nuevo nivel. Contacta al administrador.")
                         except Exception as e:
                             st.error(f"Error al intentar guardar el nuevo nivel: {e}")

                    # Marcar que el feedback y el ajuste de nivel ya se realizaron para esta ronda
                    st.session_state.feedback_given = True
                    # No necesitamos rerun aquí, el botón "Siguiente Texto" lo hará.

                # --- Botón para Pasar a la Siguiente Ronda ---
                # Este botón debe aparecer solo después de que se hayan mostrado los resultados
                if st.button("➡️ Ir al Siguiente Texto", key="next_text_button", use_container_width=True):
                    # Limpiar el estado de la ronda actual para forzar la generación de nuevo contenido
                    st.session_state.current_text = None
                    st.session_state.current_questions = None
                    st.session_state.user_answers = {}
                    st.session_state.submitted_answers = False
                    # st.session_state.score = 0 # Ya se resetea al generar nuevo texto
                    # st.session_state.feedback_given = False # Ya se resetea al generar nuevo texto
                    st.rerun() # Recargar para volver al estado donde se muestra el botón "Siguiente Texto" (o "Comenzar")

# --- Footer (Opcional) ---
st.markdown("---")
st.caption("v1.1.0 - Práctica de lectura adaptativa | Desarrollado con Streamlit y Google Gemini")
