import streamlit as st
import requests
import json
import os
import firebase_admin
from firebase_admin import credentials, firestore, initialize_app

# --- Configuration Firebase (IMPORTANT : À configurer par l'utilisateur) ---
# Pour connecter Streamlit à Firestore, vous avez besoin d'un fichier de clé de compte de service.
# 1. Allez sur la console Firebase de votre projet.
# 2. Allez dans "Paramètres du projet" (Project settings) -> "Comptes de service" (Service accounts).
# 3. Cliquez sur "Générer une nouvelle clé privée" (Generate new private key) et téléchargez le fichier JSON.
# 4. Placez ce fichier JSON (par exemple, 'votre_cle_firebase.json') dans le même dossier que votre script Streamlit.
#    OU, pour une meilleure sécurité en production, définissez le contenu de ce fichier comme une variable d'environnement.

# Chemin vers votre fichier de clé de compte de service Firebase
# Remplacez 'votre_cle_firebase.json' par le nom de votre fichier téléchargé.
# Si vous utilisez Streamlit Cloud, vous pouvez stocker le contenu du JSON dans st.secrets.
FIREBASE_CREDENTIALS_PATH = 'votre_cle_firebase.json' # <-- MODIFIEZ CECI

# --- Configuration de l'API Gemini (IMPORTANT : À configurer par l'utilisateur) ---
# Obtenez votre clé API Gemini depuis Google AI Studio ou Google Cloud Console.
# Pour une meilleure sécurité, stockez-la dans les secrets de Streamlit (st.secrets) ou comme variable d'environnement.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "VOTRE_CLE_API_GEMINI_ICI") # <-- MODIFIEZ CECI OU DÉFINISSEZ LA VARIABLE D'ENVIRONNEMENT
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent"

# --- Initialisation de Firebase (Gérée pour s'exécuter une seule fois) ---
# Utilise st.session_state pour s'assurer que Firebase n'est initialisé qu'une seule fois par session.
if 'firebase_initialized' not in st.session_state:
    st.session_state.firebase_initialized = False

if not st.session_state.firebase_initialized:
    try:
        # Vérifie si le fichier de clé existe
        if os.path.exists(FIREBASE_CREDENTIALS_PATH):
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
            initialize_app(cred)
            st.session_state.firebase_initialized = True
            st.success("Connexion à Firebase réussie.")
        else:
            st.error(f"Erreur : Le fichier de clé Firebase '{FIREBASE_CREDENTIALS_PATH}' est introuvable. "
                     "Veuillez le télécharger et le placer dans le même dossier que ce script, ou vérifier le chemin.")
            # Ne pas initialiser si le fichier n'est pas là
            st.session_state.firebase_initialized = False
    except Exception as e:
        st.error(f"Erreur lors de l'initialisation de Firebase : {e}")
        st.session_state.firebase_initialized = False

# Initialise le client Firestore seulement si Firebase a été initialisé avec succès
db = None
if st.session_state.firebase_initialized:
    db = firestore.client()
else:
    st.warning("Firestore n'est pas disponible car Firebase n'a pas pu être initialisé.")


# --- Variables de session Streamlit ---
if 'processed_documents' not in st.session_state:
    st.session_state.processed_documents = []
if 'user_id' not in st.session_state:
    # Pour cette démo, nous utilisons un ID utilisateur simple.
    # En production, vous utiliseriez un système d'authentification réel.
    st.session_state.user_id = "utilisateur_unique_demo" # Vous pouvez le changer ou le générer dynamiquement
if 'ai_response' not in st.session_state:
    st.session_state.ai_response = "La réponse de l'IA apparaîtra ici."

# --- Fonctions de stockage Firestore ---
def get_user_documents_ref(user_id):
    """Retourne la référence du document Firestore pour les documents de l'utilisateur."""
    # Note: __app_id n'est pas disponible dans Streamlit. Utilisez un ID d'application fixe ou configurez-le.
    app_id_for_firestore = "mon_ia_personnalisee" # ID d'application fixe pour Firestore
    return db.collection(f"artifacts/{app_id_for_firestore}/users/{user_id}/my_ai_documents").document('user_docs_data')

def load_documents_from_firestore():
    """Charge les documents de l'utilisateur depuis Firestore."""
    if not st.session_state.get('firebase_initialized') or db is None:
        st.warning("Firebase non initialisé ou client Firestore non disponible. Impossible de charger les documents.")
        return

    doc_ref = get_user_documents_ref(st.session_state.user_id)
    try:
        doc_snap = doc_ref.get()
        if doc_snap.exists:
            data = doc_snap.to_dict()
            if data and 'content' in data:
                st.session_state.processed_documents = data['content']
                st.success(f"Documents chargés : {len(st.session_state.processed_documents)} segments.")
            else:
                st.session_state.processed_documents = []
                st.info("Aucun document trouvé en mémoire.")
        else:
            st.session_state.processed_documents = []
            st.info("Aucun document trouvé en mémoire.")
    except Exception as e:
        st.error(f"Erreur lors du chargement des documents depuis Firestore : {e}")

def save_documents_to_firestore():
    """Sauvegarde les documents de l'utilisateur dans Firestore."""
    if not st.session_state.get('firebase_initialized') or db is None:
        st.warning("Firebase non initialisé ou client Firestore non disponible. Impossible de sauvegarder les documents.")
        return

    doc_ref = get_user_documents_ref(st.session_state.user_id)
    try:
        content_to_save = st.session_state.processed_documents
        json_string = json.dumps(content_to_save)

        if len(json_string.encode('utf-8')) > 1024 * 1024: # Vérifie la taille en octets (limite 1MB)
            st.warning("Attention : Vos documents sont trop volumineux pour être sauvegardés entièrement (limite de 1Mo par document Firestore). Seule une partie pourrait être conservée.")

        doc_ref.set({'content': content_to_save})
        st.success(f"Documents sauvegardés : {len(st.session_state.processed_documents)} segments.")
    except Exception as e:
        st.error(f"Erreur lors de la sauvegarde des documents dans Firestore : {e}")

# --- Fonctions de traitement de texte ---
def chunk_text(text, chunk_size=500, overlap=50):
    """Découpe le texte en morceaux gérables."""
    chunks = []
    i = 0
    while i < len(text):
        end = min(i + chunk_size, len(text))
        # Essayer de terminer le morceau à une limite de phrase ou de paragraphe
        if end < len(text):
            last_period = text.rfind('.', i, end)
            last_newline = text.rfind('\n', i, end)
            last_space = text.rfind(' ', i, end)

            best_break = max(last_period, last_newline, last_space)
            if best_break > i + chunk_size / 2: # S'assurer que la coupure n'est pas trop tôt
                end = best_break + 1
        chunks.append(text[i:end].strip())
        i += (chunk_size - overlap)
        if i >= len(text) - overlap and i < len(text):
            if chunks[-1] != text[i:].strip():
                chunks.append(text[i:].strip())
            break
    return [chunk for chunk in chunks if chunk] # Supprime les morceaux vides

def process_and_save_new_content(new_content):
    """Traite le nouveau contenu, le combine avec l'existant et sauvegarde."""
    if not new_content.strip():
        st.warning("Veuillez fournir du texte à ajouter.")
        return

    # Combine le nouveau contenu avec les documents existants
    combined_text = "\n\n".join(st.session_state.processed_documents) + "\n\n" + new_content
    
    # Redécoupe l'ensemble du texte combiné
    st.session_state.processed_documents = chunk_text(combined_text)
    save_documents_to_firestore()

# --- Fonction d'appel à l'API Gemini ---
def call_gemini_api(prompt):
    """Appelle l'API Gemini avec le prompt donné."""
    if not GEMINI_API_KEY or GEMINI_API_KEY == "VOTRE_CLE_API_GEMINI_ICI":
        st.error("Veuillez configurer votre clé API Gemini dans le code.")
        return "Erreur de configuration de l'API."

    headers = {
        'Content-Type': 'application/json'
    }
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}]
    }
    params = {
        "key": GEMINI_API_KEY
    }

    retries = 0
    max_retries = 3
    base_delay = 1 # seconds

    while retries < max_retries:
        try:
            response = requests.post(GEMINI_API_URL, headers=headers, params=params, json=payload)
            response.raise_for_status() # Lève une exception pour les codes d'état HTTP d'erreur (4xx ou 5xx)

            result = response.json()
            if result.get('candidates') and result['candidates'][0].get('content') and result['candidates'][0]['content'].get('parts'):
                return result['candidates'][0]['content']['parts'][0]['text']
            else:
                st.error(f"Structure de réponse inattendue de l'API Gemini : {result}")
                return "Désolé, la réponse de l'IA est inattendue."
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429: # Too Many Requests
                retries += 1
                delay = base_delay * (2 ** (retries - 1))
                st.warning(f"Limite de débit atteinte. Réessai dans {delay}s... (Tentative {retries}/{max_retries})")
                import time
                time.sleep(delay)
                continue
            else:
                st.error(f"Erreur HTTP lors de l'appel API Gemini : {e}")
                return "Désolé, une erreur est survenue lors de la communication avec l'IA."
        except Exception as e:
            st.error(f"Erreur lors de l'appel API Gemini : {e}")
            return "Désolé, une erreur inattendue est survenue."
    return "Échec de la requête API après plusieurs tentatives."

# --- Interface utilisateur Streamlit ---
st.set_page_config(page_title="Votre IA Personnalisée", layout="centered")

st.title("🤖 Votre IA Personnalisée")
st.markdown("""
Bienvenue dans votre propre IA ! Fournissez-lui des documents et posez-lui des questions.
Elle se souviendra de tout ce que vous lui donnez.
""")

# --- Section 1: Fournir les documents ---
st.header("1. Fournissez vos documents")
st.write("Vous pouvez coller du texte ou télécharger un ou plusieurs fichiers texte (.txt). L'IA utilisera ces informations pour répondre à vos questions.")

document_input = st.text_area("Collez votre texte ici...", height=200, key="doc_text_area")
uploaded_files = st.file_uploader("Ou téléchargez un ou plusieurs fichiers .txt", type="txt", accept_multiple_files=True)

if st.button("Charger et Mettre à Jour les Documents"):
    combined_content = document_input.strip()
    if uploaded_files:
        for uploaded_file in uploaded_files:
            bytes_data = uploaded_file.read()
            string_data = bytes_data.decode('utf-8')
            if combined_content:
                combined_content += "\n\n" + string_data.strip()
            else:
                combined_content = string_data.strip()
    
    if combined_content:
        with st.spinner("Traitement et sauvegarde des documents..."):
            process_and_save_new_content(combined_content)
        st.session_state.doc_text_area = "" # Clear textarea after processing
    else:
        st.warning("Veuillez coller du texte ou sélectionner des fichiers à charger.")

st.info(f"Votre IA a actuellement **{len(st.session_state.processed_documents)}** segments de documents en mémoire.")

# --- Section 2: Poser une question ---
st.header("2. Posez une question à votre IA")
question_input = st.text_input("Tapez votre question ici...", key="question_input")

if st.button("Demander à l'IA"):
    if not question_input.strip():
        st.warning("Veuillez poser une question.")
    elif not st.session_state.processed_documents:
        st.warning("Veuillez d'abord charger et traiter vos documents.")
    else:
        with st.spinner("L'IA réfléchit..."):
            # Concaténer tous les morceaux comme contexte pour l'IA
            # Note: Pour de très grands volumes, une recherche de similarité serait plus efficace.
            max_context_chars = 30000 * 4 # Estimation grossière de la limite de tokens
            context = "\n\n".join(st.session_state.processed_documents)
            if len(context) > max_context_chars:
                context = context[:max_context_chars]
                st.warning("Le contexte a été tronqué car il est trop long pour l'IA. La réponse pourrait être moins précise.")

            prompt = f"""En vous basant uniquement sur les extraits de document suivants, répondez à la question. Si la réponse ne peut pas être trouvée dans les documents fournis, indiquez-le.

Documents:
{context}

Question: {question_input}

Réponse:"""
            
            ai_response = call_gemini_api(prompt)
            st.session_state.ai_response = ai_response
        st.session_state.question_input = "" # Clear question input after asking

# --- Section 3: Réponse de l'IA ---
st.header("3. Réponse de l'IA")
st.markdown(st.session_state.ai_response) # Utilise st.session_state.ai_response pour afficher la réponse

# Charger les documents au démarrage de l'application Streamlit
# (Cette fonction est appelée une fois par session)
if 'initial_load_done' not in st.session_state:
    load_documents_from_firestore()
    st.session_state.initial_load_done = True
