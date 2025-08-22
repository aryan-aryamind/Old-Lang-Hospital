from flask import Flask, request, jsonify, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.request_validator import RequestValidator
import os
import logging
import json
from datetime import datetime
from dotenv import load_dotenv
import re
from rapidfuzz import process, fuzz
import dateparser
from sms import send_sms
import requests
from model import summarize, is_bye, extract_date, extract_time, is_confirm, detect_language, is_lab_test, is_appointment, is_yes  # Added is_yes import
import psycopg2
import csv

# Import LangChain RAG and Google TTS
from chain import ConversationalRAGChain
from langchain_rag import create_hospital_rag
from google_tts import GoogleCloudTTS

# Import blueprints
from doctor_appointments import bp_doctor
from lab_appointments import bp_lab, get_lab_db_connection, insert_lab_booking, is_lab_slot_booked, get_available_lab_test_timings

user_sessions = {}  # {call_sid: {step, doctor, department, ...}}

load_dotenv()

# Set up Google Cloud credentials for TTS
if os.path.exists("service-account-key.json"):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "service-account-key.json"
    print("✅ Google Cloud credentials loaded from service-account-key.json")
else:
    print("⚠️ Warning: service-account-key.json not found. TTS may not work.")

account_sid = os.environ["TWILIO_SID"]
auth_token = os.environ["TWILIO_AUTH"]
from_number = os.environ["TWILIO_NUMBER"]
to_number = os.environ["TO_NUMBER"]
ADMISSION_JSON = r"admision.json"
API = "http://127.0.0.1:8000/ask"

pdf_path = "D:\RAG_hospital\RAG\shalby_main.pdf"

# Initialize LangChain RAG and Google TTS
try:
    rag_chain = create_hospital_rag()
    rag = ConversationalRAGChain(pdf_path=pdf_path)
    tts = GoogleCloudTTS(cache_dir="static/audio_cache")
    print("✅ LangChain RAG and Google TTS initialized successfully!")
except Exception as e:
    print(f"⚠️ Warning: Could not initialize RAG/TTS: {e}")
    rag_chain = None
    tts = None

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

pdf_path = "D:\RAG_working\RAG\shalby_main.pdf"
bot = ConversationalRAGChain(pdf_path=pdf_path)
tts = GoogleCloudTTS(cache_dir="static/audio_cache")

app = Flask(__name__)
app.config['STATIC_FOLDER'] = 'static'

# Twilio authentication
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
VALIDATE_REQUESTS = os.environ.get("VALIDATE_REQUESTS", "false").lower() == "true"

# Add these at the top or where other intent helpers are defined
no_words = ["no", "nope", "nah", "not now", "don't", "do not", "cancel", "stop"]

# Add at the top, after user_sessions definition
LANGUAGE_OPTIONS = {
    'english': {
        'language_code': 'en-IN',
        'voice_name': 'en-IN-Standard-A',
    },
    'hindi': {
        'language_code': 'hi-IN',
        'voice_name': 'hi-IN-Standard-A',
    }
}

MESSAGES = {
    'choose_language': {
        'english': "Which language do you want to speak: Hindi or English?",
        'hindi': "आप कौन सी भाषा बोलना चाहते हैं: हिंदी या अंग्रेजी?"
    },
    'welcome': {
        'english': "Hello, I am your AI Voice Assistant! Welcome to ABC Hospital. How can I help you?",
        'hindi': "नमस्ते, मैं आपकी एआई वॉयस असिस्टेंट हूँ! एबीसी हॉस्पिटल में आपका स्वागत है। मैं आपकी कैसे मदद कर सकती हूँ?"
    },
    'fallback': {
        'english': "We didn't receive any input. Thank you for calling. Goodbye!",
        'hindi': "हमें आपकी कोई प्रतिक्रिया नहीं मिली। कॉल करने के लिए धन्यवाद। अलविदा!"
    },
    'clarification': {
        'english': "I didn't catch that. Could you please repeat what you'd like to know about our hospital?",
        'hindi': "माफ़ कीजिए, मैं समझ नहीं पाई। कृपया दोबारा बताएं कि आप हमारे अस्पताल के बारे में क्या जानना चाहते हैं?"
    },
    'appointment_prompt': {
        'english': "Great! Let's book an appointment. I'll help you through the process.",
        'hindi': "बढ़िया! चलिए अपॉइंटमेंट बुक करते हैं। मैं आपको इस प्रक्रिया में मदद करूंगी।"
    },
    'department_prompt': {
        'english': "Please tell me which department you'd like to visit.",
        'hindi': "कृपया बताएं कि आप किस विभाग में जाना चाहते हैं।"
    },
    'date_prompt': {
        'english': "For which date do you want the appointment? Please say the date in the format 22 July 2025 or 22-07-2025.",
        'hindi': "आप किस तारीख के लिए अपॉइंटमेंट चाहते हैं? कृपया तारीख बताएं जैसे 22 जुलाई 2025 या 22-07-2025।"
    },
    'time_prompt': {
        'english': "At what time? You can say 3pm, 14:00, or 2:30 p.m.",
        'hindi': "किस समय? आप 3 बजे, 14:00, या 2:30 बजे कह सकते हैं।"
    },
    'name_prompt': {
        'english': "Can you please share your good name for the booking?",
        'hindi': "कृपया बुकिंग के लिए अपना नाम बताएं।"
    },
    'mobile_prompt': {
        'english': "Now, please enter your 10 digit mobile number using the keypad.",
        'hindi': "अब कृपया कीपैड का उपयोग करके अपना 10 अंकों का मोबाइल नंबर दर्ज करें।"
    },
    'confirmation': {
        'english': "Is this correct? Please say yes or no.",
        'hindi': "क्या यह सही है? कृपया हाँ या ना कहें।"
    },
    'booking_success': {
        'english': "Your appointment has been booked successfully. Thank you!",
        'hindi': "आपका अपॉइंटमेंट सफलतापूर्वक बुक हो गया है। धन्यवाद!"
    },
    'goodbye': {
        'english': "Thank you for calling. Have a great day! Goodbye!",
        'hindi': "कॉल करने के लिए धन्यवाद। आपका दिन शुभ हो! अलविदा!"
    },
    'department_not_found': {
        'english': "Sorry, I didn't recognize that department. Available departments are: ",
        'hindi': "माफ़ कीजिए, मैं वह विभाग नहीं समझ पाई। उपलब्ध विभाग हैं: "
    },
    'confirm_department': {
        'english': "Did you mean {department}? Please say yes or no.",
        'hindi': "क्या आप {department} की बात कर रहे हैं? कृपया हाँ या ना कहें।"
    },
    'slot_booked': {
        'english': "Sorry, that slot is already booked. The next available slot is at {time}.",
        'hindi': "माफ़ कीजिए, वह स्लॉट पहले से बुक है। अगला उपलब्ध स्लॉट {time} बजे का है।"
    },
    # Add more messages as needed
}

def get_message(key, lang):
    return MESSAGES[key][lang]

# Local lab helper for listing tests to avoid 404 when redirecting to /collect-lab-test
def get_lab_test_names():
    try:
        with open('lab_tests.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            tests = data.get('tests') or data.get('lab_tests') or []
            names = []
            for t in tests:
                # schema uses key 'name'
                name = t.get('name') or t.get('test_name')
                if name:
                    names.append(name)
            return sorted(set(names))
    except Exception as e:
        logger.error(f"Error reading lab_tests.json: {e}")
        return []

def is_lab_intent(text, lang='english'):
    """Detect lab-test related intent with English and Hindi keywords."""
    try:
        t = (text or '').lower()
        keywords_en = [
            'lab test', 'blood test', 'cbc', 'lipid', 'thyroid', 'lft', 'kft',
            'vitamin d', 'urine test', 'scan', 'x-ray', 'mri', 'ct scan',
            'ultrasound', 'ecg', 'health checkup', 'package'
        ]
        keywords_hi = [
            'लैब', 'लेब', 'टेस्ट', 'वेब टेक्स्ट', 'लैब टेक्स्ट', 'लेब टेस्ट', 'खून', 'ब्लड', 'जांच', 'जाँच', 'सीबीसी', 'लिपिड', 'थायरॉयड',
            'एलएफटी', 'केएफटी', 'विटामिन', 'मूत्र', 'स्कैन', 'एक्स-रे', 'एमआरआई', 'सीटी',
            'अल्ट्रासाउंड', 'ईसीजी', 'हेल्थ चेकअप', 'पैकेज'
        ]
        if any(k in t for k in keywords_en):
            return True
        if any(k in t for k in keywords_hi):
            return True
        # Fuzzy match common phrases (to handle ASR like "वेब टेक्स्ट")
        candidates = ['लैब टेस्ट', 'लेब टेस्ट', 'वेब टेक्स्ट', 'lab test', 'blood test']
        try:
            from rapidfuzz import fuzz
            for phrase in candidates:
                if fuzz.partial_ratio(phrase.lower(), t) >= 80:
                    return True
        except Exception:
            pass
        # Also match against known test names
        for name in get_lab_test_names():
            if name.lower() in t:
                return True
        return False
    except Exception:
        return False

def normalize_speech_text(text: str) -> str:
    """Normalize ASR text: strip punctuation (including Devanagari danda), lowercase, collapse spaces."""
    if not text:
        return ''
    import re
    t = text.strip().lower()
    # Remove common punctuation including Devanagari danda and quotes
    t = re.sub(r"[\.,!?;:'\"“"।]|\s+", lambda m: ' ' if m.group(0).isspace() else ' ', t)
    t = re.sub(r"\s+", ' ', t).strip()
    return t

def get_lab_test_alias_map() -> dict:
    """Provide Hindi/short aliases mapped to canonical test names to handle ASR variants."""
    aliases = {}
    # Canonical names from file
    names = get_lab_test_names()
    # Predefined common mappings
    def add(keys, canonical):
        for k in keys:
            aliases[k.lower()] = canonical
    add(['cbc', 'सीबीसी', 'कम्प्लीट ब्लड काउंट', 'ब्लड काउंट'], 'Complete Blood Count (CBC)')
    add(['लिपिड', 'लिपिड प्रोफाइल', 'cholesterol', 'कोलेस्ट्रॉल'], 'Lipid Profile')
    add(['ब्लड शुगर', 'शुगर टेस्ट', 'फास्टिंग शुगर', 'पी पी'], 'Blood Sugar Test (Fasting/PP)')
    add(['थायरॉयड', 'टीथ्री', 'टीफोर', 'टीएसएच', 'थाइरॉइड'], 'Thyroid Function Test (T3, T4, TSH)')
    add(['एलएफटी', 'लिवर टेस्ट', 'लिवर फंक्शन'], 'Liver Function Test (LFT)')
    add(['केएफटी', 'किडनी टेस्ट', 'किडनी फंक्शन'], 'Kidney Function Test (KFT)')
    add(['विटामिन डी', 'vitamin d'], 'Vitamin D Test')
    add(['मूत्र टेस्ट', 'यूरिन टेस्ट', 'यूरिन रूटीन'], 'Urine Routine Test')
    add(['एक्स-रे', 'एक्सरे', 'छाती का x-ray'], 'X-Ray Chest')
    add(['एमआरआई', 'mri', 'एमआरआई ब्रेन'], 'MRI Brain')
    add(['सीटी', 'ct scan', 'सीटी स्कैन पेट', 'सीटी एब्डोमेन'], 'CT Scan Abdomen')
    add(['अल्ट्रासाउंड', 'अल्ट्रासाउंड एब्डोमेन', 'sonography'], 'Ultrasound Abdomen')
    add(['ईसीजी', 'ecg'], 'ECG')
    add(['हेल्थ चेकअप', 'बेसिक हेल्थ चेकअप'], 'Basic Health Checkup')
    add(['फुल बॉडी चेकअप', 'एडवांस्ड फुल बॉडी'], 'Advanced Full Body Checkup')
    add(['एग्जीक्यूटिव हेल्थ पैकेज', 'executive package'], 'Executive Health Package')
    # Map exact canonical names to themselves
    for n in names:
        aliases[n.lower()] = n
    return aliases

def generate_tts_audio_lang(text, lang):
    lang_conf = LANGUAGE_OPTIONS[lang]
    return generate_tts_audio(text, voice_name=lang_conf['voice_name'], language_code=lang_conf['language_code'])

def create_language_gather(action, method='POST', lang='english'):
    """Create a gather with language-specific settings"""
    resp = VoiceResponse()
    print("gather working")
    
    # Handle 'multi' language case for initial prompt
    if lang == 'multi':
        gather = Gather(
            input='speech',
            action=action,
            method=method,
            timeout=10,
            speechTimeout='auto',
            language='en-IN',
            bargeIn=True
        )
    else:
        lang_conf = LANGUAGE_OPTIONS[lang]
        gather = Gather(
            input='speech',
            action=action,
            method=method,
            timeout=10,
            speechTimeout='auto',
            language=lang_conf['language_code'],
            bargeIn=True
        )
    
    
    return resp, gather

def speak_message(resp, message, lang='english'):
    """Speak a message in the specified language"""
    lang_conf = LANGUAGE_OPTIONS[lang]
    audio = generate_tts_audio(message, 
                             voice_name=lang_conf['voice_name'],
                             language_code=lang_conf['language_code'])
    print("english audio")
    if audio:
        resp.play(audio)
        print("played")
    else:
        resp.say(message, language=lang_conf['language_code'])
        print("not played")

def clean_text(text):
    text = text.replace("\n", " ")
    text = re.sub(r"[*•]+\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text

def generate_tts_audio(text, voice_name='en-IN-Standard-A', language_code='en-IN'):
    """
    Generate TTS audio using Google Cloud TTS with FEMALE voice only.
    
    Args:
        text: Text to convert to speech
        voice_name: Google TTS voice name (defaults to female Indian English)
        language_code: Language code for TTS
        
    Returns:
        Audio file path or None if failed
    """
    try:
        if not text or not text.strip():
            logger.warning("Empty text provided for TTS")
            return None
            
        if tts:
            # Validate and clean language code
            valid_language_codes = ['en-IN', 'hi-IN']
            if not language_code or language_code not in valid_language_codes:
                language_code = 'en-IN'  # Default to English
                logger.info(f"Invalid language code '{language_code}', defaulting to 'en-IN'")
            
            # Always use female voice - override any male voice requests
            if 'Standard-B' in voice_name or 'Standard-D' in voice_name:
                voice_name = 'en-IN-Standard-A'  # Force female voice
            
            # Ensure voice name matches language code
            if language_code == 'hi-IN' and not voice_name.startswith('hi-IN'):
                voice_name = 'hi-IN-Standard-A'  # Use Hindi female voice
            elif language_code == 'en-IN' and not voice_name.startswith('en-IN'):
                voice_name = 'en-IN-Standard-A'  # Use English female voice
            
            audio_path = tts.generate_speech(
                text=text,
                voice_name=voice_name,
                language_code=language_code,
                speaking_rate=1.0,
                pitch=0.0,
                volume_gain_db=0.0
            )
            if audio_path:
                # Normalize to web path and return absolute URL for Twilio
                web_path = str(audio_path).replace('\\', '/')
                if web_path.startswith('static/'):
                    web_path = '/' + web_path
                try:
                    base = request.url_root.rstrip('/')
                    absolute_url = f"{base}{web_path}"
                except Exception:
                    # Fallback when no request context
                    absolute_url = web_path
                logger.info(f"Generated TTS audio with female voice: {web_path}")
                return absolute_url
        logger.warning("TTS not available, falling back to Twilio TTS. Check Google Cloud credentials and billing setup.")
        return None
    except Exception as e:
        logger.error(f"Error generating TTS audio: {e}")
        return None

def create_timeout_gather(input_type='speech', action='', method='POST', barge_in=True, 
                         primary_timeout=12, secondary_timeout=8, primary_message="", 
                         secondary_message="Are you still there? Would you like to ask anything else? I would be happy to help you."):
    """
    Helper function to create consistent timeout behavior with Google TTS and FEMALE voice only.
    
    Args:
        input_type: 'speech' or 'dtmf'
        action: The endpoint to call
        method: HTTP method
        barge_in: Whether to allow barge-in
        primary_timeout: First timeout in seconds (10-15 seconds)
        secondary_timeout: Second timeout in seconds (8-10 seconds)
        primary_message: The main prompt message
        secondary_message: The "Are you still there?" message
    """
    resp = VoiceResponse()
    
    # Primary gather with main message
    gather = Gather(
        input=input_type,
        action=action,
        method=method,
        timeout=primary_timeout,
        speechTimeout='auto',
        language='en-IN',
        bargeIn=barge_in
    )
    
    # Generate TTS audio for primary message with FEMALE voice
    primary_audio = generate_tts_audio(primary_message)
    if primary_audio:
        gather.play(primary_audio)
    else:
        # Fallback to Twilio TTS only if Google TTS completely fails
        gather.say(primary_message)
    
    resp.append(gather)
    
    # Secondary gather with "Are you still there?" message
    gather2 = Gather(
        input=input_type,
        action=action,
        method=method,
        timeout=secondary_timeout,
        speechTimeout='auto',
        language='en-IN',
        bargeIn=barge_in
    )
    
    # Generate TTS audio for secondary message with FEMALE voice
    secondary_audio = generate_tts_audio(secondary_message)
    if secondary_audio:
        gather2.play(secondary_audio)
    else:
        # Fallback to Twilio TTS only if Google TTS completely fails
        gather2.say(secondary_message)
    
    resp.append(gather2)
    
    # Final fallback message with FEMALE voice
    fallback_message = "We didn't receive any input. Thank you for calling. Goodbye!"
    fallback_audio = generate_tts_audio(fallback_message)
    
    if fallback_audio:
        resp.play(fallback_audio)
    else:
        # Fallback to Twilio TTS only if Google TTS completely fails
        resp.say(fallback_message)
    resp.hangup()
    
    return resp

def create_tts_gather(input_type='speech', action='', method='POST', barge_in=True, 
                     timeout=10, message=""):
    """
    Helper function to create a Gather with Google TTS and FEMALE voice only.
    
    Args:
        input_type: 'speech' or 'dtmf'
        action: The endpoint to call
        method: HTTP method
        barge_in: Whether to allow barge-in
        timeout: Timeout in seconds
        message: The message to speak
    """
    resp = VoiceResponse()
    
    gather = Gather(
        input=input_type,
        action=action,
        method=method,
        timeout=timeout,
        speechTimeout='auto',
        language='en-IN',
        bargeIn=barge_in
    )
    
    # Generate TTS audio with FEMALE voice
    audio = generate_tts_audio(message)
    if audio:
        gather.play(audio)
    else:
        # Fallback to Twilio TTS only if Google TTS completely fails
        gather.say(message)
    
    resp.append(gather)
    return resp

def create_tts_response(message=""):
    """
    Helper function to create a VoiceResponse with Google TTS and FEMALE voice only.
    
    Args:
        message: The message to speak
    """
    resp = VoiceResponse()
    
    # Generate TTS audio with FEMALE voice
    audio = generate_tts_audio(message)
    if audio:
        resp.play(audio)
    else:
        # Fallback to Twilio TTS only if Google TTS completely fails
        resp.say(message)
    
    return resp

def handle_rag_query(speech_input: str, call_sid: str = None) -> tuple:
    """Handle user queries through the LangChain RAG system with language support"""
    if not rag_chain and not rag:
        error_msg = "I'm sorry, the intelligent assistant is currently unavailable."
        if call_sid and user_sessions.get(call_sid, {}).get('language') == 'hindi':
            error_msg = "माफ़ कीजिए, इंटेलिजेंट असिस्टेंट वर्तमान में उपलब्ध नहीं है।"
        return error_msg, None
    
    try:
        # Detect language from input
        session = user_sessions.get(call_sid, {})
        current_lang = session.get('language', 'english')
        logger.info(f"RAG: call_sid={call_sid}, lang={current_lang}, question={speech_input}")
        
        # Check if the query is incomplete
        speech_input_clean = speech_input.strip()
        
        # Handle incomplete queries with language support
        incomplete_indicators = {
            'english': [
                "I want to know about",
                "I want to know",
                "Tell me about",
                "What about",
                "Can you tell me",
                "I need to know",
                "I would like to know"
            ],
            'hindi': [
                "मैं जानना चाहता हूं",
                "मुझे बताइए",
                "क्या आप बता सकते हैं",
                "मैं जानना चाहूंगा",
                "कृपया बताएं"
            ]
        }
        
        is_incomplete = False
        for indicator in incomplete_indicators.get(current_lang, []):
            if speech_input_clean.lower().startswith(indicator.lower()) and len(speech_input_clean) < len(indicator) + 10:
                is_incomplete = True
                break
        
        if is_incomplete or len(speech_input_clean) < 10:
            text_response = get_message('clarification', current_lang)
            logger.info(f"RAG: incomplete query → reply={text_response}")
        else:
            # Prefer HospitalRAGChain for robust retrieval (PDF + data) across languages
            if rag_chain:
                logger.info("RAG: using HospitalRAGChain")
                text_response = rag_chain.invoke(speech_input)
            elif rag:
                logger.info("RAG: using fallback ConversationalRAGChain")
            text_response = rag.invoke(speech_input)
            else:
                text_response = get_message('clarification', current_lang)
            text_response = clean_text(text_response)
            logger.info(f"RAG: answer={text_response}")
        
        # Generate audio in the correct language
        audio_path = None
        if tts:
            audio_path = tts.generate_speech(text_response, language_code=LANGUAGE_OPTIONS[current_lang]['language_code'])
            logger.info(f"RAG: tts_audio_path={audio_path}")
        
        return text_response, audio_path
        
    except Exception as e:
        logger.error(f"RAG query error: {e}")
        error_msg = "I'm sorry, I couldn't process your request. Please try again."
        if call_sid and user_sessions.get(call_sid, {}).get('language') == 'hindi':
            error_msg = "माफ़ कीजिए, मैं आपका अनुरोध प्रोसेस नहीं कर पाई। कृपया पुनः प्रयास करें।"
        return error_msg, None

def validate_twilio_request():
    """Validate that the request is from Twilio"""
    if not VALIDATE_REQUESTS:
        return True
        
    if not TWILIO_AUTH_TOKEN:
        logger.warning("TWILIO_AUTH_TOKEN not set, skipping validation")
        return True
        
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    
    # Get the request URL and POST data
    request_url = str(request.url)
    request_data = request.form
    
    # Get X-Twilio-Signature header
    signature = request.headers.get('X-TWILIO-SIGNATURE', '')
    
    return validator.validate(request_url, request_data, signature)

@app.before_request
def before_request():
    """Validate all requests are from Twilio"""
    if request.method == 'POST':
        if not validate_twilio_request():
            logger.warning("Invalid Twilio signature")
            return jsonify({"error": "Invalid request signature"}), 403


# Main routes

@app.route('/test-voice', methods=['GET', 'POST'])
def test_voice():
    """Simple test endpoint to verify voice webhook functionality"""
    try:
        resp = VoiceResponse()
        resp.say("Hello! This is a test of the voice webhook. The server is working correctly.")
        resp.hangup()
        return str(resp)
    except Exception as e:
        logger.error(f"Error in test voice endpoint: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/voice', methods=['GET','POST'])
def voice_webhook():
    try:
        call_sid = request.values.get('CallSid')
        speech_result = request.values.get('SpeechResult', '').strip()
        
        logger.info(f"Voice webhook called - CallSid: {call_sid}, Speech: '{speech_result}'")
        
        # Initialize session if new call
        if call_sid not in user_sessions:
            user_sessions[call_sid] = {
                'step': 'welcome',
                'language': 'english',  # Default language
                'conversation_count': 0
            }
            logger.info(f"New session created for CallSid: {call_sid}")
        
        session = user_sessions[call_sid]
        current_lang = session.get('language', 'english')
        
        logger.info(f"Processing call - Step: {session['step']}, Language: {current_lang}")
        
        resp = VoiceResponse()

        # Language selection handling
        if session['step'] == 'choose_language' or session['step'] == 'welcome':
            if 'hindi' in speech_result.lower() or 'हिंदी' in speech_result.lower():
                session['language'] = 'hindi'
                current_lang = 'hindi'
                session['step'] = 'main'
                logger.info(f"Language set to Hindi for CallSid: {call_sid}")
            elif 'english' in speech_result.lower() or 'इंग्लिश' in speech_result.lower():
                session['language'] = 'english'
                current_lang = 'english'
                session['step'] = 'main'
                logger.info(f"Language set to English for CallSid: {call_sid}")
            
            if session['step'] == 'welcome':
                # Initial language selection prompt
                logger.info(f"Providing language selection prompt for CallSid: {call_sid}")
                resp, gather = create_language_gather('/voice', 'POST')
                speak_message(gather, get_message('choose_language', 'english'), 'english')
                speak_message(gather, get_message('choose_language', 'hindi'), 'hindi')
                resp.append(gather)
                session['step'] = 'choose_language'
                user_sessions[call_sid] = session
                return str(resp)

        # Welcome message after language selection
        if session['step'] == 'main' and session.get('welcomed') != True:
            session['welcomed'] = True
            welcome_message = get_message('welcome', current_lang)
            logger.info(f"Providing welcome message for CallSid: {call_sid}")
            resp, gather = create_language_gather('/voice', 'POST', current_lang)
            speak_message(gather, welcome_message, current_lang)
            resp.append(gather)
            user_sessions[call_sid] = session
            return str(resp)
        
        # Handle empty or very short speech results
        if not speech_result or len(speech_result.strip()) < 3:
            logger.info(f"Empty or very short speech detected: '{speech_result}' for CallSid: {call_sid}")
            resp = VoiceResponse()
            gather = Gather(
                input='speech',
                action='/voice',
                method='POST',
                timeout=12,
                speechTimeout='auto',
                language=LANGUAGE_OPTIONS[current_lang]['language_code'],
                bargeIn=True
            )
            clarification_message = get_message('clarification', current_lang)
            speak_message(gather, clarification_message, current_lang)
            resp.append(gather)
            
            fallback_message = get_message('fallback', current_lang)
            speak_message(resp, fallback_message, current_lang)
            resp.hangup()
            return str(resp)

        # Check for goodbye
        if is_bye(speech_result):
            logger.info(f"Goodbye detected for CallSid: {call_sid}")
            goodbye_message = get_message('goodbye', current_lang)
            resp = VoiceResponse()
            speak_message(resp, goodbye_message, current_lang)
            resp.hangup()
            return str(resp)
        
        # LAB booking intent (must run before any other routing) - Hindi + English
        if speech_result and (is_lab_test(speech_result) or is_lab_intent(speech_result, current_lang)):
            logger.info(f"Lab test intent detected: '{speech_result}' for CallSid: {call_sid}")
            resp = VoiceResponse()
            # Avoid duplicate prompting here; let /collect-lab-test handle the prompt and list
            resp.redirect('/collect-lab-test', method='POST')
            return str(resp)

        # Handle RAG queries for general information (guard against booking intents)
        # If Hindi/English general queries come in, route to RAG unless it's an appointment or lab booking intent
        if (
            (session['step'] == 'welcome' or True) and
            not is_appointment(speech_result) and
            not is_lab_test(speech_result) and
            not is_lab_intent(speech_result, current_lang)
        ):
            
            logger.info(f"RAG query intent detected: '{speech_result}' for CallSid: {call_sid}")
            text_response, audio_path = handle_rag_query(speech_result, call_sid)
            
            resp = VoiceResponse()
            # Speak response and prompt inside a Gather so barge-in works during playback
            gather = Gather(
                input='speech',
                action='/voice',
                method='POST',
                timeout=12,
                speechTimeout='auto',
                language=LANGUAGE_OPTIONS[current_lang]['language_code'],
                bargeIn=True
            )
            
            # Use the audio path from RAG if available, otherwise generate new TTS
            if audio_path and tts:
                gather.play(audio_path)
            else:
                # Generate TTS for the text response
                rag_audio = generate_tts_audio(text_response)
                if rag_audio:
                    gather.play(rag_audio)
                else:
                    speak_message(gather, text_response, current_lang)
            
            # Generate TTS for the follow-up question
            follow_up_message_en = "Would you like to book an appointment with one of our doctors? Say yes to continue or ask me anything else about our hospital."
            follow_up_message_hi = "क्या आप हमारे किसी डॉक्टर के साथ अपॉइंटमेंट बुक करना चाहेंगे? जारी रखने के लिए हाँ कहें या हमारे अस्पताल के बारे में कुछ और पूछें।"
            follow_up_message = follow_up_message_hi if current_lang == 'hindi' else follow_up_message_en
            speak_message(gather, follow_up_message, current_lang)
            
            resp.append(gather)
            # Fallback
            fallback_message = get_message('fallback', current_lang)
            speak_message(resp, fallback_message, current_lang)
            resp.hangup()
            return str(resp)
        
        # Handle appointment booking flow
        if is_appointment(speech_result):
            logger.info(f"Appointment intent detected: '{speech_result}' for CallSid: {call_sid}")
            session['step'] = 'department_selection'
            resp = VoiceResponse()
            
            # Build the appointment booking message
            appointment_message = get_message('appointment_prompt', current_lang)
            
            # Get departments from RAG system
            if rag_chain:
                try:
                    doctors = rag_chain.get_available_doctors()
                    departments = list(set([d.get('doctor_department', '') for d in doctors if d.get('doctor_department')]))
                    dept_text = ", ".join(departments[:5])  # Limit to first 5 for voice
                    if dept_text:
                        appointment_message += f" Available departments include: {dept_text}."
                except Exception as e:
                    logger.warning(f"Could not get departments from RAG: {e}")
            
            appointment_message += " " + get_message('department_prompt', current_lang)
            
            # Generate TTS audio for appointment booking message
            # Speak using language-specific TTS
            
            # Move prompts inside Gather to allow barge-in
            gather = Gather(
                input='speech',
                action='/collect-department',
                method='POST',
            timeout=12,
            speechTimeout='auto',
            language=LANGUAGE_OPTIONS[current_lang]['language_code'],
            bargeIn=True
            )
            speak_message(gather, appointment_message, current_lang)
            
            resp.append(gather)
            fallback_message = get_message('fallback', current_lang)
            speak_message(resp, fallback_message, current_lang)
            resp.hangup()
            return str(resp)
        
        # Default response for unrecognized input
        logger.info(f"Unrecognized input, providing help: '{speech_result}' for CallSid: {call_sid}")
        resp = VoiceResponse()
        # Default help inside a Gather to allow barge-in on the prompt
        gather = Gather(
            input='speech',
            action='/voice',
            method='POST',
            timeout=12,
            speechTimeout='auto',
            language=LANGUAGE_OPTIONS[current_lang]['language_code'],
            bargeIn=True
        )
        
        # Generate TTS for default help message
        help_message_en = "I didn't understand that. You can ask me about our hospital services, doctors, or say 'book appointment' to schedule a visit."
        help_message_hi = "मैं समझ नहीं पाई। आप हमारे अस्पताल की सेवाओं, डॉक्टरों के बारे में पूछ सकते हैं, या 'बुक अपॉइंटमेंट' कहकर समय निर्धारित कर सकते हैं।"
        help_message = help_message_hi if current_lang == 'hindi' else help_message_en
        speak_message(gather, help_message, current_lang)
        
        resp.append(gather)
        fallback_message = get_message('goodbye', current_lang)
        speak_message(resp, fallback_message, current_lang)
        resp.hangup()
        return str(resp)
        
    except Exception as e:
        logger.error(f"Error in voice webhook: {e}")
        # Always return a valid TwiML response even on error
        resp = VoiceResponse()
        session = user_sessions.get(request.values.get('CallSid', ''), {})
        current_lang = session.get('language', 'english')
        error_message = "I'm sorry, there was an error processing your request. Please try calling again."
        if current_lang == 'hindi':
            error_message = "माफ़ कीजिए, आपके अनुरोध को प्रोसेस करने में त्रुटि हुई। कृपया पुनः कॉल करें।"
        
        speak_message(resp, error_message, current_lang)
        resp.hangup()
        return str(resp)

def parse_booking_request(text):
    """Extract department, date, and time from user input using regex and keywords."""
    department = None
    date = None
    time = None
    # Departments from doctors_list.json
    departments = get_departments_from_doctors_list()
    department = best_match_department(text, departments)
    # Date (robust: look for dd-mm-yyyy, dd Month yyyy, dd Month, Month dd, etc.)
    date_match = re.search(r'(\d{1,2}[\-/ ]?(?:[A-Za-z]+)?[\-/ ]?(\d{2,4})?)', text)
    if date_match:
        date_str = date_match.group(0)
        parsed = dateparser.parse(date_str)
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    else:
        # Try to parse any date in the text
        parsed = dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future'})
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    # Time (robust: look for \d{1,2}(:\d{2})? ?[ap]m or 24h)
    time_match = re.search(r'(\d{1,2}:\d{2}|\d{1,2} ?[ap]m)', text.lower())
    if time_match:
        time_str = time_match.group(1).replace(' ', '').upper()
        if ':' not in time_str and len(time_str) <= 4:
            hour_match = re.match(r'(\d{1,2})', time_str)
            if hour_match:
                hour = int(hour_match.group(1))
                suffix = 'AM' if 'AM' in time_str else 'PM'
                if suffix == 'PM' and hour != 12:
                    hour += 12
                slot = f"{hour:02d}:00-{hour:02d}:30"
                time = slot
        else:
            # Try to match to slot format
            parts = time_str.split(':')
            if len(parts) == 2:
                hour = int(parts[0])
                minute = int(parts[1][:2])
                slot = f"{hour:02d}:{minute:02d}-{hour:02d}:{minute+30:02d}"
                time = slot
    return department, date, time

def best_match_department(user_text, departments, threshold=60):
    # Lower threshold for better matching
    result = process.extractOne(user_text, departments, scorer=fuzz.token_sort_ratio, score_cutoff=threshold)
    if result:
        match, score, _ = result
        logger.info(f"Fuzzy department match: '{user_text}' -> '{match}' (score: {score})")
        return match if score >= threshold else None
    logger.info(f"No fuzzy department match for: '{user_text}'")
    return None

def extract_any_date(text, lang='english'):
    """Enhanced date parsing with Hindi support"""
    if lang == 'hindi':
        # Hindi date patterns
        hindi_months = {
            'जनवरी': 'january',
            'फरवरी': 'february',
            'मार्च': 'march',
            'अप्रैल': 'april',
            'मई': 'may',
            'जून': 'june',
            'जुलाई': 'july',
            'अगस्त': 'august',
            'सितंबर': 'september',
            'अक्टूबर': 'october',
            'नवंबर': 'november',
            'दिसंबर': 'december'
        }
        
        # Convert Hindi month names to English
        for hindi_month, english_month in hindi_months.items():
            text = text.replace(hindi_month, english_month)
    
    # Use non-capturing groups in regex
    date_candidates = re.findall(r'\d{1,2}(?:st|nd|rd|th)?\s*(?:of)?\s*[A-Za-z]+|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', text)
    for candidate in date_candidates:
        if not isinstance(candidate, str):
            candidate = ' '.join(candidate)
        parsed = dateparser.parse(candidate)
        if parsed:
            return parsed.strftime('%Y-%m-%d')
    
    # Fallback to whole text
    parsed = dateparser.parse(text)
    if parsed:
        return parsed.strftime('%Y-%m-%d')
    return None

def convert_hindi_numbers(text):
    """Convert Hindi numerals to English numerals"""
    hindi_numbers = {
        '०': '0', '१': '1', '२': '2', '३': '3', '४': '4',
        '५': '5', '६': '6', '७': '7', '८': '8', '९': '9'
    }
    for hindi_num, eng_num in hindi_numbers.items():
        text = text.replace(hindi_num, eng_num)
    return text

# --- In /server-rag, add lab test booking intent detection and redirect ---
@app.route('/server-rag',methods=['POST'])
def server_rag():
    rag_question = request.values.get('SpeechResult', '')
    logger.info(f"User input speech to rag: {rag_question}")

    resp = VoiceResponse()

    # --- Lab test reschedule intent detection (must come before booking intent) ---
    if 'reschedule' in rag_question.lower() and 'lab test' in rag_question.lower():
        resp.redirect('/reschedule-lab-test', method='POST')
        return str(resp)

    # --- Lab test booking intent detection ---
    lab_keywords = ['lab test', 'book lab test', 'blood test', 'health checkup', 'scan', 'package']
    test_names = get_lab_test_names()
    if any(kw in rag_question.lower() for kw in lab_keywords) or any(test.lower() in rag_question.lower() for test in test_names):
        test_list = ', '.join(test_names)
        # Speak the available lab tests inside a Gather with barge_in=True using language
        call_sid = request.values.get('CallSid')
        session = user_sessions.get(call_sid, {})
        current_lang = session.get('language', 'english')
        gather = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=10, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        prompt_en = f"We have the following lab tests available: {test_list}. Which one would you like to book?"
        prompt_hi = f"हमारे पास ये लैब टेस्ट उपलब्ध हैं: {test_list}. आप कौन-सा टेस्ट बुक करना चाहते हैं?"
        speak_message(gather, prompt_hi if current_lang == 'hindi' else prompt_en, current_lang)
        resp.append(gather)
        # Add a second prompt if no response
        gather2 = Gather(input='speech', action='/collect-lab-test', method='POST', barge_in=True, timeout=8, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        prompt2_en = "Are you still there? Please say the lab test name."
        prompt2_hi = "क्या आप अभी भी लाइन पर हैं? कृपया लैब टेस्ट का नाम बताएं।"
        speak_message(gather2, prompt2_hi if current_lang == 'hindi' else prompt2_en, current_lang)
        resp.append(gather2)
        speak_message(resp, get_message('fallback', current_lang), current_lang)
        resp.hangup()
        return str(resp)

@app.route('/collect-lab-test', methods=['POST'])
def collect_lab_test():
    """Collect the lab test name from user and prompt next step."""
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language', 'english')
    spoken_test = request.values.get('SpeechResult', '').strip()
    tests = get_lab_test_names()
    alias_map = get_lab_test_alias_map()
    resp = VoiceResponse()
    if not tests:
        msg_en = "Sorry, lab test list is not available right now."
        msg_hi = "क्षमा कीजिए, अभी लैब टेस्ट सूची उपलब्ध नहीं है।"
        speak_message(resp, msg_hi if current_lang == 'hindi' else msg_en, current_lang)
        resp.hangup()
        return str(resp)
    # Normalize and alias match first (handles Hindi and ASR variants like 'लेटेस्ट')
    norm = normalize_speech_text(spoken_test)
    canonical = alias_map.get(norm)
    # If not direct alias, try partial alias and then fuzzy on canonical names
    if not canonical:
        # Check substring in alias keys
        for k, v in alias_map.items():
            if k in norm:
                canonical = v
                break
    if not canonical:
        # Fuzzy match test name
        match = process.extractOne(spoken_test, tests, scorer=fuzz.token_sort_ratio)
        if match and match[1] >= 75:
            canonical = match[0]
    if not canonical:
        test_list = ', '.join(tests[:8])
        gather = Gather(input='speech', action='/collect-lab-test', method='POST', timeout=10, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        msg_en = f"Available lab tests include: {test_list}. Please say the test name."
        msg_hi = f"उपलब्ध लैब टेस्ट में शामिल हैं: {test_list}. कृपया टेस्ट का नाम बोलें।"
        speak_message(gather, msg_hi if current_lang == 'hindi' else msg_en, current_lang)
        resp.append(gather)
        return str(resp)
    # Save selection and move forward (placeholder: return confirmation)
    session['lab_test'] = canonical
    user_sessions[call_sid] = session
    # Proceed to date collection like doctor flow
    gather = Gather(input='speech', action='/collect-lab-date', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
    en = f"For {canonical}, which date would you like? Please say the date, for example 22 July 2025."
    hi = f"{canonical} के लिए आप किस तारीख पर टेस्ट कराना चाहेंगे? कृपया तारीख बताएं, जैसे 22 जुलाई 2025।"
    speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
    resp.append(gather)
    return str(resp)

@app.route('/collect-lab-date', methods=['POST'])
def collect_lab_date():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language','english')
    date_text = request.values.get('SpeechResult','')
    date = extract_any_date(date_text, lang=current_lang)
    resp = VoiceResponse()
    if not date:
        gather = Gather(input='speech', action='/collect-lab-date', method='POST', timeout=10, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, "क्षमा कीजिए, तारीख समझ में नहीं आई। कृपया अपनी नई तारीख बताएं।" if current_lang=='hindi' else "Sorry, I didn't understand the date. Please say the date.", current_lang)
        resp.append(gather)
        return str(resp)
    session['lab_date'] = date
    user_sessions[call_sid] = session
    # Build time slots from lab timings
    test_name = session.get('lab_test')
    timings = get_available_lab_test_timings(test_name) or ''
    # Ask for time
    gather = Gather(input='speech', action='/collect-lab-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
    en = f"On {date}, what time would you like your {test_name}?"
    hi = f"{date} को आप {test_name} किस समय कराना चाहेंगे?"
    speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
    resp.append(gather)
    return str(resp)

@app.route('/collect-lab-time', methods=['POST'])
def collect_lab_time():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language','english')
    time_text = request.values.get('SpeechResult','')
    test_name = session.get('lab_test')
    date = session.get('lab_date')
    resp = VoiceResponse()
    # Build slot list from timings window (30-min slots)
    timings = get_available_lab_test_timings(test_name)
    slot_list = []
    if timings:
        import re
        from datetime import datetime, timedelta
        match = re.match(r'(\d{1,2}:\d{2} [APMapm]{2}) to (\d{1,2}:\d{2} [APMapm]{2})', timings)
        if match:
            start_str, end_str = match.groups()
            start_dt = datetime.strptime(start_str.upper(), '%I:%M %p')
            end_dt = datetime.strptime(end_str.upper(), '%I:%M %p')
            t = start_dt
            while t < end_dt:
                slot_start = t.strftime('%H:%M')
                slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                slot_list.append(f"{slot_start}-{slot_end}")
                t += timedelta(minutes=30)
    # Extract slot from speech using enhanced Hindi time extraction
    def extract_time_slot(text):
        # First try enhanced Hindi time extraction
        extracted_time = extract_time(text, current_lang)
        if extracted_time:
            slot_start = extracted_time
            for slot in slot_list:
                if slot.startswith(slot_start):
                    return slot
        # Fallback to dateparser
        import dateparser
        parsed = dateparser.parse(text)
        if parsed:
            slot_start = parsed.strftime('%H:%M')
            for slot in slot_list:
                if slot.startswith(slot_start):
                    return slot
        # Direct slot matching
        for slot in slot_list:
            if text.strip() in slot:
                return slot
        return None
    slot_val = extract_time_slot(time_text)
    if not slot_val:
        slots_str = ', '.join(slot_list[:8]) if slot_list else ''
        gather = Gather(input='speech', action='/collect-lab-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        en = f"Available 30 minute slots are: {slots_str}. Please say a valid time."
        hi = f"उपलब्ध 30 मिनट के स्लॉट हैं: {slots_str}. कृपया वैध समय बोलें।"
        speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
        resp.append(gather)
        return str(resp)
    # Check booking
    if is_lab_slot_booked(test_name, date, slot_val):
        # suggest next available
        from datetime import datetime
        requested_time = datetime.strptime(slot_val.split('-')[0], '%H:%M')
        next_slot = None
        for slot in slot_list:
            slot_time = datetime.strptime(slot.split('-')[0], '%H:%M')
            if slot_time > requested_time and not is_lab_slot_booked(test_name, date, slot):
                next_slot = slot
                break
        gather = Gather(input='speech', action='/confirm-lab-suggested', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        en = f"Sorry, that slot is already booked. The next available slot is at {next_slot}. Would you like to book this? Please say yes or no."
        hi = f"क्षमा कीजिए, वह स्लॉट पहले से बुक है। अगला उपलब्ध स्लॉट {next_slot} है। क्या आप इसे बुक करना चाहेंगे? कृपया हाँ या ना कहें।"
        session['lab_suggested_time'] = next_slot
        session['lab_time'] = None
        user_sessions[call_sid] = session
        speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
        resp.append(gather)
        return str(resp)
    session['lab_time'] = slot_val
    user_sessions[call_sid] = session
    # Confirm details
    gather = Gather(input='speech', action='/confirm-lab', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
    en = f"You want to book {test_name} on {date} at {slot_val}. Is this correct? Please say yes or no."
    hi = f"आप {test_name} {date} को {slot_val} पर बुक करना चाहते हैं। क्या यह सही है? कृपया हाँ या ना कहें।"
    speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
    resp.append(gather)
    return str(resp)

@app.route('/confirm-lab', methods=['POST'])
def confirm_lab():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language','english')
    answer = request.values.get('SpeechResult','').strip().lower()
    yes = is_yes(answer, current_lang)
    resp = VoiceResponse()
    if yes:
        # Ask name
        gather = Gather(input='speech', action='/collect-lab-name', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, get_message('name_prompt', current_lang), current_lang)
        resp.append(gather)
        return str(resp)
    else:
        # Ask time again
        gather = Gather(input='speech', action='/collect-lab-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, get_message('time_prompt', current_lang), current_lang)
        resp.append(gather)
        return str(resp)

@app.route('/collect-lab-name', methods=['POST'])
def collect_lab_name():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language','english')
    name = request.values.get('SpeechResult','').strip()
    resp = VoiceResponse()
    if not name:
        gather = Gather(input='speech', action='/collect-lab-name', method='POST', timeout=10, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, get_message('name_prompt', current_lang), current_lang)
        resp.append(gather)
        return str(resp)
    session['lab_name'] = name
    user_sessions[call_sid] = session
    gather = Gather(input='dtmf', num_digits=10, action='/confirm-lab-mobile', method='POST', timeout=15)
    speak_message(gather, get_message('mobile_prompt', current_lang), current_lang)
    resp.append(gather)
    return str(resp)

@app.route('/confirm-lab-mobile', methods=['POST'])
def confirm_lab_mobile():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language','english')
    digits = request.values.get('Digits','')
    resp = VoiceResponse()
    if len(digits) != 10:
        gather = Gather(input='dtmf', num_digits=10, action='/confirm-lab-mobile', method='POST', timeout=15)
        msg_en = "That was not a valid mobile number. Please enter your 10 digit mobile number using the keypad."
        msg_hi = "यह एक मान्य मोबाइल नंबर नहीं था। कृपया कीपैड का उपयोग करके अपना 10 अंकों का मोबाइल नंबर दर्ज करें।"
        speak_message(gather, msg_hi if current_lang=='hindi' else msg_en, current_lang)
        resp.append(gather)
        return str(resp)
    session['lab_mobile'] = digits
    user_sessions[call_sid] = session
    # Finalize booking
    test_name = session.get('lab_test')
    date = session.get('lab_date')
    time = session.get('lab_time')
    name = session.get('lab_name')
    if is_lab_slot_booked(test_name, date, time):
        gather = Gather(input='speech', action='/collect-lab-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, get_message('slot_booked', current_lang).format(time=time), current_lang)
        resp.append(gather)
        return str(resp)
    try:
        logger.info(f"Attempting to book lab test: {test_name}, {date}, {time}, {name}, {digits}")
        insert_lab_booking(test_name, date, time, name, digits, True)
        logger.info(f"Lab booking successful for {test_name} on {date} at {time}")
        speak_message(resp, "आपका लैब टेस्ट बुक हो गया है। धन्यवाद!" if current_lang=='hindi' else "Your lab test has been booked. Thank you!", current_lang)
        resp.hangup()
        return str(resp)
    except Exception as e:
        logger.error(f"Lab booking failed: {e}")
        # Fallback: save to JSON file
        try:
            booking_data = {
                'test_name': test_name,
                'date': date,
                'time': time,
                'name': name,
                'mobile': digits,
                'home_collection': True
            }
            with open('lab_bookings.json', 'r', encoding='utf-8') as f:
                bookings = json.load(f)
            bookings.append(booking_data)
            with open('lab_bookings.json', 'w', encoding='utf-8') as f:
                json.dump(bookings, f, indent=2)
            logger.info(f"Lab booking saved to JSON file as fallback")
            speak_message(resp, "आपका लैब टेस्ट बुक हो गया है। धन्यवाद!" if current_lang=='hindi' else "Your lab test has been booked. Thank you!", current_lang)
            resp.hangup()
            return str(resp)
        except Exception as json_error:
            logger.error(f"JSON fallback also failed: {json_error}")
            speak_message(resp, "क्षमा कीजिए, बुकिंग में समस्या आई। कृपया पुनः प्रयास करें।" if current_lang=='hindi' else "Sorry, there was an error booking your lab test. Please try again.", current_lang)
        resp.hangup()
        return str(resp)

    # --- Existing doctor appointment logic ---
    if 'book' in rag_question.lower() and 'appointment' in rag_question.lower():
        # Try to parse all details from the utterance
        department, date, time = parse_booking_request(rag_question)
        if not department:
            # If not enough info, ask for department
            call_sid = request.values.get('CallSid')
            user_sessions[call_sid] = {'step': 'department'}
            departments = get_departments_from_doctors_list()
            dept_list = ', '.join(departments)
            primary_message = f"Which department do you want to book an appointment in? Available departments are: {dept_list}."
            secondary_message = "Are you still there? Please say the department name."
            
            return str(create_timeout_gather(
                input_type='speech',
                action='/collect-department',
                method='POST',
                barge_in=True,
                primary_timeout=10,
                secondary_timeout=8,
                primary_message=primary_message,
                secondary_message=secondary_message
            ))
        if department and date and time:
            # Find all available doctors for that slot
            all_slots = get_available_slots_for_department_and_date(department, date)
            available_doctors = sorted(list(set(s['doctor'] for s in all_slots if s['time'] == time)))
            if available_doctors:
                call_sid = request.values.get('CallSid')
                # If only one doctor, ask for confirmation
                if len(available_doctors) == 1:
                    slot = {
                        'doctor': available_doctors[0],
                        'department': get_department_by_doctor_name(available_doctors[0]) or department,
                        'date': date,
                        'time': time
                    }
                    user_sessions[call_sid] = {'step': 'confirm', **slot}
                    message = f"{slot['doctor']} is available in {slot['department']} on {date} at {time}. Would you like to book with {slot['doctor']}? Please say yes or no."
                    
                    return str(create_tts_gather(
                        input_type='speech',
                        action='/confirm-booking',
                        method='POST',
                        barge_in=True,
                        timeout=10,
                        message=message
                    ))
                else:
                    # Multiple doctors available, list them and ask for confirmation for the first
                    slot = {
                        'doctor': available_doctors[0],
                        'department': get_department_by_doctor_name(available_doctors[0]) or department,
                        'date': date,
                        'time': time
                    }
                    user_sessions[call_sid] = {'step': 'confirm', **slot}
                    doc_list = ', '.join([f"Dr. {d}" for d in available_doctors])
                    message = f"The following doctors are available in {slot['department']} on {date} at {time}: {doc_list}. Would you like to book with {slot['doctor']}? Please say yes or no."
                    
                    return str(create_tts_gather(
                        input_type='speech',
                        action='/confirm-booking',
                        method='POST',
                        barge_in=True,
                        timeout=10,
                        message=message
                    ))
            else:
                # Suggest nearest slot
                all_slots = get_available_slots_for_department_and_date(department, date)
                suggestion = next((s for s in all_slots), None) # Simplified suggestion
                if suggestion:
                    call_sid = request.values.get('CallSid')
                    # Use department from doctors_list.json if possible
                    suggestion_department = get_department_by_doctor_name(suggestion['doctor']) or department
                    user_sessions[call_sid] = {'step': 'suggest', **suggestion, 'department': suggestion_department}
                    message = f"No doctor is available at that time. The nearest available slot is with {suggestion['doctor']} in {suggestion_department} on {date} at {suggestion['time']}. Do you want to book this slot? Please say yes or no."
                    
                    return str(create_tts_gather(
                        input_type='speech',
                        action='/confirm-booking',
                        method='POST',
                        barge_in=True,
                        timeout=10,
                        message=message
                    ))
                else:
                    message = "Sorry, no slots are available in that department. Thank you."
                    resp = create_tts_response(message)
                    resp.hangup()
                    return str(resp)
        else:
            # If not enough info, ask for department
            call_sid = request.values.get('CallSid')
            user_sessions[call_sid] = {'step': 'department'}
            departments = get_departments_from_doctors_list()
            dept_list = ', '.join(departments)
            primary_message = f"Which department do you want to book an appointment in? Available departments are: {dept_list}."
            secondary_message = "Are you still there? Please say the department name."
            
            return str(create_timeout_gather(
                input_type='speech',
                action='/collect-department',
                method='POST',
                barge_in=True,
                primary_timeout=10,
                secondary_timeout=8,
                primary_message=primary_message,
                secondary_message=secondary_message
            ))

    # RAG fallback
    try:
        if is_bye(rag_question):
            resp.say("Thank you!", voice='alice')
            resp.hangup()
            return str(resp)

        answer = bot.invoke(rag_question)
        logger.info(f"RAG answer: {answer}")
        
        language = detect_language(answer)
        logger.info(f"Detected language: {language}")
        speech = tts.generate_speech(text=answer,language_code=language)
        logger.info(f"Speech path: {speech}")
        gather = Gather(
                input='speech',
                action='/server-rag',
                method='POST',
                barge_in=True
            )
        gather.play(speech)
        resp.append(gather)
    except Exception as e:
        logger.error(f"Error calling RAG API: {e}")
        resp = create_tts_response("Sorry, I'm having trouble accessing the information right now.")
    return str(resp)

# Helper: Get departments from doctors_list.json
with open('doctors_list.json', 'r', encoding='utf-8') as f:
    DOCTORS_LIST = json.load(f)["doctors"]

def get_departments_from_doctors_list():
    return sorted(set(doc["doctor_department"] for doc in DOCTORS_LIST))

def get_doctors_by_department_from_list(department):
    return [doc for doc in DOCTORS_LIST if doc["doctor_department"].lower() == department.lower()]

def get_department_by_doctor_name(doctor_name):
    for doc in DOCTORS_LIST:
        if doc["doctor_name"].lower() == doctor_name.lower():
            return doc["doctor_department"]
    return None

def get_valid_times_for_department(department):
    from datetime import datetime, timedelta
    # Union of all available times for all doctors in the department, minus lunch break
    valid_times = set()
    for doc in DOCTORS_LIST:
        if doc['doctor_department'].lower() == department.lower():
            # Parse available time range
            start_str, end_str = doc['doctor_available_time'].replace(' ', '').split('to')
            start_dt = datetime.strptime(start_str, '%H')
            end_dt = datetime.strptime(end_str, '%H')
            # Build 30-min slots
            t = start_dt
            while t < end_dt:
                slot_start = t.strftime('%H:%M')
                slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                valid_times.add(f"{slot_start}-{slot_end}")
                t += timedelta(minutes=30)
            # Remove lunch break slots
            lunch = doc.get('lunch_break')
            if lunch:
                lunch_start, lunch_end = lunch.replace(' ', '').split('-')
                lunch_start_dt = datetime.strptime(lunch_start, '%H:%M')
                lunch_end_dt = datetime.strptime(lunch_end, '%H:%M')
                t = lunch_start_dt
                while t < lunch_end_dt:
                    slot_start = t.strftime('%H:%M')
                    slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                    valid_times.discard(f"{slot_start}-{slot_end}")
                    t += timedelta(minutes=30)
    return sorted(valid_times)

def get_available_slots_for_department_and_date(department, date):
    from datetime import datetime, timedelta
    doctors_in_dept = [doc for doc in DOCTORS_LIST if doc['doctor_department'].lower() == department.lower()]
    booked_slots_for_date = set()
    try:
        with open('bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for b in bookings:
                if b.get('date') == date:
                    booked_slots_for_date.add((b.get('doctor'), b.get('time')))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    all_available_slots = []
    for doc_info in doctors_in_dept:
        doc_name = doc_info['doctor_name']
        try:
            start_str, end_str = doc_info['doctor_available_time'].replace(' ', '').split('to')
            start_hour = int(start_str)
            if start_hour < 7: start_hour += 12
            end_hour = int(end_str)
            if end_hour <= 7: end_hour += 12
            start_dt = datetime.strptime(str(start_hour), '%H')
            end_dt = datetime.strptime(str(end_hour), '%H')
        except (ValueError, KeyError):
            logger.error(f"Could not parse available_time for {doc_name}")
            continue
        lunch_start_dt, lunch_end_dt = None, None
        if lunch := doc_info.get('lunch_break'):
            try:
                lunch_start_str, lunch_end_str = lunch.replace(' ', '').split('-')
                lunch_start_dt = datetime.strptime(lunch_start_str, '%H:%M')
                lunch_end_dt = datetime.strptime(lunch_end_str, '%H:%M')
            except (ValueError, KeyError):
                logger.error(f"Could not parse lunch_break for {doc_name}")
        current_time = start_dt
        while current_time < end_dt:
            slot_start_time = current_time
            slot_end_time = current_time + timedelta(minutes=30)
            slot_str = f"{slot_start_time.strftime('%H:%M')}-{slot_end_time.strftime('%H:%M')}"
            is_in_lunch = lunch_start_dt and lunch_start_dt <= slot_start_time < lunch_end_dt
            is_booked_json = (doc_name, slot_str) in booked_slots_for_date
            is_booked_db = is_slot_booked(doc_name, date, slot_str)
            if not is_in_lunch and not is_booked_json and not is_booked_db:
                all_available_slots.append({'doctor': doc_name, 'time': slot_str})
            current_time += timedelta(minutes=30)
    all_available_slots.sort(key=lambda x: (x['time'], x['doctor']))
    return all_available_slots

def is_slot_booked(doctor, date, time):
    """Check if a slot is already in bookings.json or the DB."""
    # Check in bookings.json
    try:
        with open('bookings.json', 'r', encoding='utf-8') as f:
            bookings = json.load(f)
            for booking in bookings:
                if (booking.get('doctor') == doctor and
                    booking.get('date') == date and
                    booking.get('time') == time):
                    return True
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Check in PostgreSQL
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT 1 FROM bookings WHERE doctor=%s AND date=%s AND time=%s LIMIT 1""",
            (doctor, date, time)
        )
        exists = cur.fetchone() is not None
        cur.close()
        conn.close()
        if exists:
            return True
    except Exception as e:
        logger.error(f"Error checking slot in DB: {e}")
    return False

def get_db_connection():
    import os
    return psycopg2.connect(
        dbname=os.environ.get('PG_DB', 'your_db'),
        user=os.environ.get('PG_USER', 'your_user'),
        password=os.environ.get('PG_PASSWORD', 'your_password'),
        host=os.environ.get('PG_HOST', 'localhost'),
        port=os.environ.get('PG_PORT', 5432)
    )

# NOTE: Ensure you run this SQL in your DB:
# ALTER TABLE bookings ADD CONSTRAINT unique_doctor_slot UNIQUE (doctor, date, time);
def insert_booking(department, doctor, date, time, name, mobile):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO bookings (department, doctor, date, time, name, mobile)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (department, doctor, date, time, name, mobile)
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise Exception("Slot already booked")
    finally:
        cur.close()
        conn.close()

@app.route('/collect-department', methods=['POST'])
def collect_department():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language', 'english')
    spoken_dept = request.values.get('SpeechResult', '')
    departments = get_departments_from_doctors_list()
    match = process.extractOne(spoken_dept, departments, scorer=fuzz.token_sort_ratio)
    resp = VoiceResponse()
    if not match:
        dept_list = ', '.join(departments)
        message = get_message('department_not_found', current_lang) + dept_list
        resp, gather = create_language_gather('/collect-department', 'POST', current_lang)
        speak_message(gather, message, current_lang)
        resp.append(gather)
        return str(resp)
    department, score, _ = match
    if score < 85:
        session['pending_department'] = department
        user_sessions[call_sid] = session
        message = get_message('confirm_department', current_lang).format(department=department)
        resp, gather = create_language_gather('/confirm-department', 'POST', current_lang)
        speak_message(gather, message, current_lang)
        resp.append(gather)
        return str(resp)
    session['department'] = department
    user_sessions[call_sid] = session
    message = get_message('date_prompt', current_lang).format(department=department)
    resp, gather = create_language_gather('/collect-date', 'POST', current_lang)
    speak_message(gather, message, current_lang)
    resp.append(gather)
    return str(resp)

@app.route('/confirm-department', methods=['POST'])
def confirm_department():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/confirm-department: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    department = session.get('pending_department')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words) and department:
        session['department'] = department
        session.pop('pending_department', None)
        user_sessions[call_sid] = session
        primary_message = f"Great. For which date do you want the appointment in {department}? Please say the date in the format 22 july 2025 or 22-07-2025."
        secondary_message = "Are you still there? Please say the date for your appointment."
        logger.info(f"Agent: {primary_message}")
        
        return str(create_timeout_gather(
            input_type='speech',
            action='/collect-date',
            method='POST',
            barge_in=True,
            primary_timeout=12,
            secondary_timeout=8,
            primary_message=primary_message,
            secondary_message=secondary_message
        ))
    elif any(word in answer for word in no_words):
        session.pop('pending_department', None)
        user_sessions[call_sid] = session
        departments = get_departments_from_doctors_list()
        dept_list = ', '.join(departments)
        primary_message = f"Okay, please say the department again. Available departments are: {dept_list}."
        secondary_message = "Are you still there? Please say the department name."
        logger.info(f"Agent: {primary_message}")
        
        return str(create_timeout_gather(
            input_type='speech',
            action='/collect-department',
            method='POST',
            barge_in=True,
            primary_timeout=12,
            secondary_timeout=8,
            primary_message=primary_message,
            secondary_message=secondary_message
        ))
    else:
        primary_message = f"Did you mean {department}? Please say yes or no."
        secondary_message = "Are you still there? Please say yes or no."
        logger.info(f"Agent: {primary_message}")
        
        return str(create_timeout_gather(
            input_type='speech',
            action='/confirm-department',
            method='POST',
            barge_in=True,
            primary_timeout=12,
            secondary_timeout=8,
            primary_message=primary_message,
            secondary_message=secondary_message
        ))

@app.route('/collect-date', methods=['POST'])
def collect_date():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language', 'english')
    date_text = request.values.get('SpeechResult', '')
    logger.info(f"User said date: {date_text}")
    date = extract_any_date(date_text, lang=current_lang)
    if not date:
        parsed = dateparser.parse(date_text)
        if parsed:
            date = parsed.strftime('%Y-%m-%d')
    resp = VoiceResponse()
    if date:
        logger.info(f"Parsed date: {date}")
        from datetime import timedelta
        parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
        today = datetime.today().date()
        two_months_later = today + timedelta(days=30)
        if parsed_date < today or parsed_date > two_months_later:
            gather = Gather(input='speech', action='/collect-date', method='POST', language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            message = get_message('clarification', current_lang)
            speak_message(gather, message, current_lang)
            resp.append(gather)
            return str(resp)
        session['date'] = date
        user_sessions[call_sid] = session
        # Always proceed to collect time
        message = get_message('time_prompt', current_lang).format(date=date)
        return str(create_tts_gather(
            input_type='speech',
            action='/collect-time',
            method='POST',
            barge_in=True,
            timeout=10,
            message=message
        ))
    else:
        gather = Gather(input='speech', action='/collect-date', method='POST', language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        message = get_message('clarification', current_lang)
        speak_message(gather, message, current_lang)
        resp.append(gather)
        return str(resp)

def is_time_in_range(start, end, check):
    """Check if check (HH:MM) is in [start, end) (HH:MM)."""
    from datetime import datetime
    fmt = '%H:%M'
    s = datetime.strptime(start, fmt)
    e = datetime.strptime(end, fmt)
    c = datetime.strptime(check, fmt)
    return s <= c < e

# Load doctors list with lunch breaks
with open('doctors_list.json', 'r', encoding='utf-8') as f:
    DOCTORS_LIST = json.load(f)["doctors"]

def get_doctor_lunch_break(doctor_name):
    for doc in DOCTORS_LIST:
        if doc["doctor_name"].lower() == doctor_name.lower():
            return doc.get("lunch_break")
    return None

def get_next_available_slot_after_lunch(slots, lunch_end):
    from datetime import datetime
    fmt = '%H:%M'
    lunch_end_dt = datetime.strptime(lunch_end, fmt)
    for slot in slots:
        slot_start = slot["time"].split('-')[0]
        slot_start_dt = datetime.strptime(slot_start, fmt)
        if slot_start_dt >= lunch_end_dt and slot["available"]:
            return slot["time"]
    return None

@app.route('/collect-time', methods=['POST'])
def collect_time():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language', 'english')
    time_text = request.values.get('SpeechResult', '')
    logger.info(f"/collect-time: User input: {time_text}, Session: {session}")
    department = session.get('department')
    date = session.get('date')
    logger.info(f"/collect-time: Using department: {department}, date: {date}")
    available_slots = get_available_slots_for_department_and_date(department, date)
    valid_times = [slot['time'] for slot in available_slots]
    time_val = extract_time(time_text, lang=current_lang)
    matched_slots = [slot for slot in available_slots if slot['time'].split('-')[0] == time_val]
    from datetime import datetime
    resp = VoiceResponse()
    if time_val and matched_slots:
        slot = matched_slots[0]
        session['time'] = slot['time']
        session['doctor'] = slot['doctor']
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        message = get_message('confirmation', current_lang).format(doctor=slot['doctor'], department=department, date=date, time=slot['time'])
        speak_message(gather, message, current_lang)
        resp.append(gather)
        return str(resp)
    # If user requested a time but no doctor is available at that time, suggest next available slot after requested time
    if time_val and not matched_slots:
        try:
            t = datetime.strptime(time_val, '%H:%M')
        except Exception:
            t = None
        next_slot = None
        min_diff = None
        for slot in available_slots:
            slot_start, _ = slot['time'].split('-')
            slot_time = datetime.strptime(slot_start, '%H:%M')
            if t and slot_time > t:
                diff = (slot_time - t).total_seconds()
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    next_slot = slot
        if next_slot:
            gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            message = get_message('slot_booked', current_lang).format(time=next_slot['time'])
            speak_message(gather, message, current_lang)
            resp.append(gather)
            return str(resp)
        else:
            if available_slots:
                earliest_slot = available_slots[0]
                gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
                message = get_message('slot_booked', current_lang).format(time=earliest_slot['time'])
                speak_message(gather, message, current_lang)
                resp.append(gather)
                return str(resp)
            else:
                gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
                message = get_message('clarification', current_lang)
                speak_message(gather, message, current_lang)
                resp.append(gather)
                return str(resp)
    if not time_val and available_slots:
        slot = available_slots[0]
        session['time'] = slot['time']
        session['doctor'] = slot['doctor']
        user_sessions[call_sid] = session
        gather = Gather(input='speech', action='/confirm-datetime', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        message = get_message('confirmation', current_lang).format(doctor=slot['doctor'], department=department, date=date, time=slot['time'])
        speak_message(gather, message, current_lang)
        resp.append(gather)
        return str(resp)
    resp = VoiceResponse()
    slot_list = ', '.join([f"{slot['time']} with {slot['doctor']}" for slot in available_slots]) if available_slots else get_message('fallback', current_lang)
    gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
    speak_message(gather, slot_list, current_lang)
    resp.append(gather)
    return str(resp)

@app.route('/confirm-datetime', methods=['POST'])
def confirm_datetime():
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    answer = request.values.get('SpeechResult', '').strip().lower()
    logger.info(f"/confirm-datetime: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    current_lang = session.get('language', 'english')
    department = session.get('department')
    date = session.get('date')
    time = session.get('time')
    doctor = session.get('doctor')
    # yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    # no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    # if any(word in answer for word in yes_words):
    if is_yes(answer, current_lang):
        # If doctor is already set (from slot match), proceed to confirm-booking
        if doctor:
            session['step'] = 'confirm'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/confirm-booking', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
            speak_message(gather, get_message('confirmation', current_lang), current_lang)
            resp.append(gather)
            return str(resp)
        # If not, find all available doctors for that department/date/time
        available_slots = get_available_slots_for_department_and_date(department, date)
        doctors_at_time = [slot['doctor'] for slot in available_slots if slot['time'] == time]
        if len(doctors_at_time) == 1:
            session['doctor'] = doctors_at_time[0]
            session['step'] = 'confirm'
            user_sessions[call_sid] = session
            gather = Gather(input='speech', action='/confirm-booking', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
            speak_message(gather, get_message('confirmation', current_lang), current_lang)
            resp.append(gather)
            return str(resp)
        elif len(doctors_at_time) > 1:
            session['available_doctors'] = doctors_at_time
            user_sessions[call_sid] = session
            doc_list = ', '.join([f"Dr. {d}" for d in doctors_at_time])
            gather = Gather(input='speech', action='/choose-doctor', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
            # Keep dynamic English fallback, but use TTS helper for language voice
            speak_message(gather, f"The following doctors are available in {department} on {date} at {time}: {doc_list}. Which doctor would you like to book with? Please say the doctor's name.", current_lang)
            resp.append(gather)
            return str(resp)
        else:
            gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
            speak_message(gather, "Sorry, no doctors are available at that time. Please say another time.", current_lang)
            resp.append(gather)
            return str(resp)
    elif any(word in answer for word in no_words):
        # Instead of restarting, go back to time selection for same department/date
        gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, f"Okay, let's try another time. Please say the time you want for your appointment in {department} on {date}.", current_lang)
        resp.append(gather)
        return str(resp)
    else:
        agent_msg1 = f"You want to book an appointment in {department} on {date} at {time}. Is this correct? Please say yes or no."
        logger.info(f"Agent: {agent_msg1}")
        return str(create_timeout_gather(
            input_type='speech',
            action='/confirm-datetime',
            method='POST',
            barge_in=True,
            primary_timeout=12,
            secondary_timeout=8,
            primary_message=agent_msg1,
            secondary_message="Are you still there? Please say yes or no.",
            # speechModel="deepgram_nova-3",
            # language="multi"
        ))

def find_matching_slot(slots, parsed_time):
    # slots: list of slot dicts with 'time'
    # parsed_time: datetime.time object
    for slot in slots:
        start, _ = slot['time'].split('-')
        start_hour, start_minute = map(int, start.split(':'))
        if parsed_time.hour == start_hour and parsed_time.minute == start_minute:
            return slot['time']
    return None

@app.route('/confirm-booking', methods=['POST'])
def confirm_booking():
    call_sid = request.values.get('CallSid')
    answer = request.values.get('SpeechResult', '').strip().lower()
    session = user_sessions.get(call_sid, {})
    # DO NOT extract or update date here!
    logger.info(f"/confirm-booking: User response: {answer}, Session: {session}")
    resp = VoiceResponse()
    current_lang = session.get('language', 'english')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if any(word in answer for word in yes_words):
        session['step'] = 'name'
        user_sessions[call_sid] = session
        agent_msg = get_message('name_prompt', current_lang)
        logger.info(f"Agent: {agent_msg}")
        gather = Gather(input='speech', action='/collect-name', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, agent_msg, current_lang)
        resp.append(gather)
        return str(resp)
    elif any(word in answer for word in no_words):
        # Instead of ending, go back to time selection for same department/date
        department = session.get('department')
        date = session.get('date')
        gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, f"Okay, let's try another time. Please say the time you want for your appointment in {department} on {date}.", current_lang)
        resp.append(gather)
        return str(resp)
    else:
        agent_msg1 = get_message('confirmation', current_lang)
        agent_msg2 = "Are you there? Can you speak yes or no?"
        logger.info(f"Agent: {agent_msg1}")
        gather = Gather(input='speech', action='/finalize-booking', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, agent_msg1, current_lang)
        resp.append(gather)
        logger.info(f"Agent: {agent_msg2}")
        gather2 = Gather(input='speech', action='/finalize-booking', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather2, agent_msg2, current_lang)
        resp.append(gather2)
        speak_message(resp, get_message('fallback', current_lang), current_lang)
        resp.hangup()
    return str(resp)

@app.route('/collect-name', methods=['POST'])
def collect_name():
    call_sid = request.values.get('CallSid')
    name = request.values.get('SpeechResult', '')
    logger.info(f"/collect-name: User said name: {name}")
    session = user_sessions.get(call_sid, {})
    current_lang = session.get('language', 'english')
    resp = VoiceResponse()
    # Track name attempts
    attempts = session.get('name_attempts', 0)
    if not name.strip():
        attempts += 1
        session['name_attempts'] = attempts
        user_sessions[call_sid] = session
        if attempts < 2:
            gather = Gather(input='speech', action='/collect-name', method='POST', timeout=10, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            message = get_message('name_prompt', current_lang)
            speak_message(gather, message, current_lang)
            resp.append(gather)
            return str(resp)
        else:
            message = get_message('fallback', current_lang)
            speak_message(resp, message, current_lang)
            resp.hangup()
            return str(resp)
    # Reset attempts on success
    session['name'] = name
    session['step'] = 'mobile'
    session['name_attempts'] = 0
    user_sessions[call_sid] = session
    # Increase DTMF timeout to 15 seconds
    gather = Gather(input='dtmf', num_digits=10, action='/confirm-mobile', method='POST', timeout=15)
    message = get_message('mobile_prompt', current_lang)
    speak_message(gather, message, current_lang)
    resp.append(gather)
    return str(resp)

@app.route('/confirm-mobile', methods=['POST'])
def confirm_mobile():
    call_sid = request.values.get('CallSid')
    digits = request.values.get('Digits', '')
    logger.info(f"/confirm-mobile: Received digits: {digits}")
    session = user_sessions.get(call_sid, {})
    session['pending_mobile'] = digits
    user_sessions[call_sid] = session
    resp = VoiceResponse()
    try:
        if len(digits) == 10:
            # Do NOT send SMS here. Only confirm details and proceed to finalize-booking
            details = (
                f"You are booking an appointment with {session.get('doctor','')} in {session.get('department','')} on {session.get('date','')} at {session.get('time','')}. "
                f"Your name is {session.get('name','')} and your mobile number is {digits}. Is this correct? Please say yes or no."
            )
            gather = Gather(input='speech', action='/finalize-booking', method='POST', timeout=10)
            speak_message(gather, details, session.get('language','english'))
            resp.append(gather)
        else:
            logger.warning(f"/confirm-mobile: Invalid mobile number entered: {digits}")
            gather = Gather(input='dtmf', num_digits=10, action='/confirm-mobile', method='POST', timeout=15)
            msg_en = "That was not a valid mobile number. Please enter your 10 digit mobile number using the keypad."
            msg_hi = "यह एक मान्य मोबाइल नंबर नहीं था। कृपया कीपैड का उपयोग करके अपना 10 अंकों का मोबाइल नंबर दर्ज करें।"
            speak_message(gather, msg_hi if session.get('language','english')=='hindi' else msg_en, session.get('language','english'))
            resp.append(gather)
    except Exception as e:
        logger.error(f"/confirm-mobile: Exception occurred: {e}")
        msg_en = "Sorry, there was an error processing your input. Please try again later."
        msg_hi = "क्षमा कीजिए, आपके इनपुट को प्रोसेस करने में त्रुटि हुई। कृपया बाद में पुनः प्रयास करें।"
        speak_message(resp, msg_hi if session.get('language','english')=='hindi' else msg_en, session.get('language','english'))
        resp.hangup()
    return str(resp)

@app.route('/finalize-booking', methods=['POST'])
def finalize_booking():
    call_sid = request.values.get('CallSid')
    answer = request.values.get('SpeechResult', '').strip().lower()
    session = user_sessions.get(call_sid, {})  # Ensure session is defined first
    resp = VoiceResponse()
    current_lang = session.get('language', 'english')
    digits = session.get('pending_mobile', '')
    doctor = session.get('doctor')
    department = session.get('department')
    date = session.get('date')
    time = session.get('time')
    name = session.get('name')
    yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
    no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
    if (is_yes(answer, current_lang) or any(word in answer for word in yes_words)) and len(digits) == 10:
        # Final check if slot was booked by someone else
        if is_slot_booked(doctor, date, time):
            booked = False
        else:
            try:
                insert_booking(department, doctor, date, time, name, digits)
                logger.info(f"Inserted booking into PostgreSQL for {doctor} on {date} at {time}")
                # Also append to bookings.json for backup/audit
                booking_data = {
                    'department': department,
                    'doctor': doctor,
                    'date': date,
                    'time': time,
                    'name': name,
                    'mobile': digits
                }
                try:
                    bookings_file = 'bookings.json'
                    if os.path.exists(bookings_file):
                        with open(bookings_file, 'r', encoding='utf-8') as f:
                            bookings = json.load(f)
                            if not isinstance(bookings, list):
                                bookings = []
                    else:
                        bookings = []
                    bookings.append(booking_data)
                    with open(bookings_file, 'w', encoding='utf-8') as f:
                        json.dump(bookings, f, indent=2)
                except Exception as e:
                    logger.error(f"Error writing to bookings.json: {e}")
                booked = True
            except Exception as e:
                if 'Slot already booked' in str(e):
                    booked = False
                else:
                    logger.error(f"Error inserting booking into DB: {e}")
                    resp.say("Sorry, there was an error booking your slot. Please try again.")
                    resp.hangup()
                    return str(resp)
        log_msg = (
            f"Attempting to book: Doctor={doctor}, Department={department}, "
            f"Date={date}, Time={time}, Name={name}, Mobile={digits}"
        )
        if booked:
            logger.info(log_msg)
            sms_msg = (
                f"Your appointment is confirmed!\n"
                f"Doctor: {doctor}\n"
                f"Department: {department}\n"
                f"Date: {date}\n"
                f"Time: {time}\n"
                f"Name: {name}\n"
                f"Mobile: {digits}"
            )
            try:
                send_sms(f"+91{digits}", sms_msg)
            except Exception as e:
                logger.error(f"Error sending SMS: {e}")
            speak_message(resp, get_message('booking_success', current_lang), current_lang)
            gather = Gather(input='speech', action='/post-booking-options', method='POST', timeout=10, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
            speak_message(gather, "Do you have any more questions to ask, or would you like to book another appointment? You can say 'book appointment', 'ask a question', or 'no'.", current_lang)
            resp.append(gather)
            return str(resp)
        else:
            logger.warning(f"Appointment booking failed: {log_msg}")
            logger.warning(f"Booking failure reason: slot was already booked")
            speak_message(resp, "Sorry, the slot was just booked by someone else. Please try again.", current_lang)
            gather = Gather(input='speech', action='/post-booking-options', method='POST', timeout=10, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
            speak_message(gather, "Do you have any more questions to ask, or would you like to book another appointment? You can say 'book appointment', 'ask a question', or 'no'.", current_lang)
            resp.append(gather)
            return str(resp)
    elif any(word in answer for word in no_words):
        # Instead of restarting, go back to time selection for same department/date
        department = session.get('department')
        date = session.get('date')
        gather = Gather(input='speech', action='/collect-time', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, f"Okay, let's try another time. Please say the time you want for your appointment in {department} on {date}.", current_lang)
        resp.append(gather)
        return str(resp)
    else:
        agent_msg1 = get_message('confirmation', current_lang)
        agent_msg2 = "Are you there? Can you speak yes or no?"
        logger.info(f"Agent: {agent_msg1}")
        gather = Gather(input='speech', action='/finalize-booking', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather, agent_msg1, current_lang)
        resp.append(gather)
        logger.info(f"Agent: {agent_msg2}")
        gather2 = Gather(input='speech', action='/finalize-booking', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'], bargeIn=True)
        speak_message(gather2, agent_msg2, current_lang)
        resp.append(gather2)
        speak_message(resp, get_message('fallback', current_lang), current_lang)
        resp.hangup()
    return str(resp)

# --- Doctor Appointment Rescheduling ---
@app.route('/reschedule-appointment', methods=['POST'])
def reschedule_appointment():
    from twilio.twiml.voice_response import VoiceResponse, Gather
    call_sid = request.values.get('CallSid')
    session = user_sessions.get(call_sid, {})
    step = session.get('reschedule_step', 'start')
    resp = VoiceResponse()
    if step == 'start':
        # Step 1: Ask for mobile number
        session['reschedule_step'] = 'get_mobile'
        user_sessions[call_sid] = session
        current_lang = session.get('language','english')
        gather = Gather(input='dtmf', num_digits=10, action='/reschedule-appointment', method='POST', timeout=15)
        msg_en = "To reschedule your appointment, please enter your 10 digit mobile number using the keypad."
        msg_hi = "अपॉइंटमेंट को रीसिड्यूल करने के लिए, कृपया कीपैड का उपयोग करके अपना 10 अंकों का मोबाइल नंबर दर्ज करें।"
        speak_message(gather, msg_hi if current_lang=='hindi' else msg_en, current_lang)
        resp.append(gather)
        return str(resp)
    elif step == 'get_mobile':
        digits = request.values.get('Digits', '')
        if len(digits) != 10:
            current_lang = session.get('language','english')
            gather = Gather(input='dtmf', num_digits=10, action='/reschedule-appointment', method='POST', timeout=15)
            msg_en = "That was not a valid mobile number. Please enter your 10 digit mobile number using the keypad."
            msg_hi = "यह एक मान्य मोबाइल नंबर नहीं था। कृपया कीपैड का उपयोग करके अपना 10 अंकों का मोबाइल नंबर दर्ज करें।"
            speak_message(gather, msg_hi if current_lang=='hindi' else msg_en, current_lang)
            resp.append(gather)
            return str(resp)
        session['reschedule_mobile'] = digits
        # Step 2: Find latest booking for this mobile
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, doctor, department, date, time, name FROM bookings WHERE mobile=%s ORDER BY date DESC, time DESC LIMIT 1", (digits,))
        booking = cur.fetchone()
        cur.close()
        conn.close()
        if not booking:
            current_lang = session.get('language','english')
            msg_en = "Sorry, no appointment was found for this mobile number. Please check and try again."
            msg_hi = "क्षमा कीजिए, इस मोबाइल नंबर के लिए कोई अपॉइंटमेंट नहीं मिला। कृपया जांच कर पुनः प्रयास करें।"
            speak_message(resp, msg_hi if current_lang=='hindi' else msg_en, current_lang)
            resp.hangup()
            return str(resp)
        session['reschedule_booking_id'] = booking[0]
        session['reschedule_doctor'] = booking[1]
        session['reschedule_department'] = booking[2]
        session['reschedule_old_date'] = booking[3]
        session['reschedule_old_time'] = booking[4]
        session['reschedule_name'] = booking[5]
        user_sessions[call_sid] = session
        # Step 3: Ask for new date
        session['reschedule_step'] = 'get_new_date'
        user_sessions[call_sid] = session
        current_lang = session.get('language','english')
        gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        en = f"Found your appointment with {booking[1]} in {booking[2]} on {booking[3]} at {booking[4]}. What new date would you like to reschedule to? Please say the date."
        hi = f"आपकी {booking[2]} में {booking[1]} के साथ {booking[3]} को {booking[4]} बजे अपॉइंटमेंट मिली है। कृपया बताएं कि आप किस नई तारीख पर रीसिड्यूल करना चाहेंगे।"
        speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
        resp.append(gather)
        return str(resp)
    elif step == 'get_new_date':
        date_text = request.values.get('SpeechResult', '')
        date = extract_any_date(date_text)
        if not date:
            current_lang = session.get('language','english')
            gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            speak_message(gather, "क्षमा कीजिए, तारीख समझ में नहीं आई। कृपया अपनी नई तारीख बताएं।" if current_lang=='hindi' else "Sorry, I didn't understand the date. Please say the new date for your appointment.", current_lang)
            resp.append(gather)
            return str(resp)
        session['reschedule_new_date'] = date
        session['reschedule_step'] = 'get_new_time'
        user_sessions[call_sid] = session
        current_lang = session.get('language','english')
        gather = Gather(input='speech', action='/reschedule-appointment', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        en = f"On {date}, what time would you like? Please say the time, for example 3pm or 14:00."
        hi = f"{date} को आप किस समय चाहते हैं? कृपया समय बताएं, जैसे 3 बजे या 14:00।"
        speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
        resp.append(gather)
        return str(resp)
    elif step == 'get_new_time':
        time_text = request.values.get('SpeechResult', '')
        # Build slot list for the test
        test_name = session['lab_reschedule_test']
        timings = get_available_lab_test_timings(test_name)
        import re
        from datetime import datetime, timedelta
        slot_list = []
        if timings:
            match = re.match(r'(\d{1,2}:\d{2} [APMapm]{2}) to (\d{1,2}:\d{2} [APMapm]{2})', timings)
            if match:
                start_str, end_str = match.groups()
                start_dt = datetime.strptime(start_str.upper(), '%I:%M %p')
                end_dt = datetime.strptime(end_str.upper(), '%I:%M %p')
                t = start_dt
                while t < end_dt:
                    slot_start = t.strftime('%H:%M')
                    slot_end = (t + timedelta(minutes=30)).strftime('%H:%M')
                    slot_list.append(f"{slot_start}-{slot_end}")
                    t += timedelta(minutes=30)
        def extract_time_slot(text):
            import dateparser
            parsed = dateparser.parse(text)
            if parsed:
                slot_start = parsed.strftime('%H:%M')
                for slot in slot_list:
                    if slot.startswith(slot_start):
                        return slot
            for slot in slot_list:
                if text.strip() in slot:
                    return slot
            return None
        slot_val = extract_time_slot(time_text)
        if not slot_val:
            slot_str = ', '.join(slot_list) if slot_list else 'No slots available.'
            current_lang = session.get('language','english')
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            en = f"Sorry, available 30 minute slots for this test are: {slot_str}. Please say a valid time."
            hi = f"क्षमा कीजिए, इस टेस्ट के लिए उपलब्ध 30 मिनट के स्लॉट हैं: {slot_str}. कृपया वैध समय बताएं।"
            speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
            resp.append(gather)
            return str(resp)
        # Step 4: Check slot availability
        date = session['lab_reschedule_new_date']
        if is_lab_slot_booked(test_name, date, slot_val):
            # Suggest next available slot
            requested_time = datetime.strptime(slot_val.split('-')[0], '%H:%M')
            next_slot = None
            for slot in slot_list:
                slot_time = datetime.strptime(slot.split('-')[0], '%H:%M')
                if slot_time > requested_time and not is_lab_slot_booked(test_name, date, slot):
                    next_slot = slot
                    break
            if not next_slot:
                for slot in slot_list:
                    if not is_lab_slot_booked(test_name, date, slot):
                        next_slot = slot
                        break
            if next_slot:
                current_lang = session.get('language','english')
                gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
                en = f"Sorry, that slot is already booked. The next available slot is at {next_slot}. Would you like to reschedule to this time? Please say yes or no."
                hi = f"क्षमा कीजिए, वह स्लॉट पहले से बुक है। अगला उपलब्ध स्लॉट {next_slot} है। क्या आप इस समय पर रीसिड्यूल करना चाहेंगे? कृपया हाँ या ना कहें।"
                speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
                session['lab_reschedule_suggested_time'] = next_slot
                session['lab_reschedule_step'] = 'confirm_suggested_time'
                user_sessions[call_sid] = session
                resp.append(gather)
                return str(resp)
            else:
                current_lang = session.get('language','english')
                speak_message(resp, "क्षमा कीजिए, इस तारीख पर इस टेस्ट के सभी स्लॉट बुक हैं। कृपया कोई दूसरी तारीख आज़माएं।" if current_lang=='hindi' else "Sorry, all slots are booked for this test on this date. Please try another date.", current_lang)
                resp.hangup()
                return str(resp)
        session['lab_reschedule_new_time'] = slot_val
        session['lab_reschedule_step'] = 'confirm_new_time'
        user_sessions[call_sid] = session
        current_lang = session.get('language','english')
        gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
        en = f"You want to reschedule your lab test {test_name} to {date} at {slot_val}. Is this correct? Please say yes or no."
        hi = f"आप अपना लैब टेस्ट {test_name} {date} को {slot_val} पर रीसिड्यूल करना चाहते हैं। क्या यह सही है? कृपया हाँ या ना कहें।"
        speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
        resp.append(gather)
        return str(resp)
    elif step == 'confirm_suggested_time':
        answer = request.values.get('SpeechResult', '').strip().lower()
        yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
        no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
        if any(word in answer for word in yes_words):
            session['lab_reschedule_new_time'] = session['lab_reschedule_suggested_time']
            session['lab_reschedule_step'] = 'confirm_new_time'
            user_sessions[call_sid] = session
            current_lang = session.get('language','english')
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            en = f"You want to reschedule your lab test to {session['lab_reschedule_new_date']} at {session['lab_reschedule_new_time']}. Is this correct? Please say yes or no."
            hi = f"आप अपना लैब टेस्ट {session['lab_reschedule_new_date']} को {session['lab_reschedule_new_time']} पर रीसिड्यूल करना चाहते हैं। क्या यह सही है? कृपया हाँ या ना कहें।"
            speak_message(gather, hi if current_lang=='hindi' else en, current_lang)
            resp.append(gather)
            return str(resp)
        elif any(word in answer for word in no_words):
            session['lab_reschedule_step'] = 'get_new_time'
            user_sessions[call_sid] = session
            current_lang = session.get('language','english')
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            speak_message(gather, "ठीक है, कृपया अपने लैब टेस्ट के लिए कोई दूसरा समय बताएं।" if current_lang=='hindi' else "Okay, please say another time for your lab test.", current_lang)
            resp.append(gather)
            return str(resp)
        else:
            current_lang = session.get('language','english')
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            speak_message(gather, "क्या आप सुझाए गए समय पर रीसिड्यूल करना चाहेंगे? कृपया हाँ या ना कहें।" if current_lang=='hindi' else "Would you like to reschedule to the suggested time? Please say yes or no.", current_lang)
            resp.append(gather)
            return str(resp)
    elif step == 'confirm_new_time':
        answer = request.values.get('SpeechResult', '').strip().lower()
        yes_words = ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay']
        no_words = ['no', 'nope', 'nah', 'not', 'incorrect', 'wrong']
        if any(word in answer for word in yes_words):
            # Step 5: Update booking in DB and JSON
            booking_id = session['lab_reschedule_booking_id']
            new_date = session['lab_reschedule_new_date']
            new_time = session['lab_reschedule_new_time']
            conn = get_lab_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE lab_bookings SET date=%s, time=%s WHERE id=%s", (new_date, new_time, booking_id))
            conn.commit()
            cur.close()
            conn.close()
            # Update lab_bookings.json
            try:
                with open('lab_bookings.json', 'r', encoding='utf-8') as f:
                    bookings = json.load(f)
                for b in bookings:
                    if b.get('mobile') == session['lab_reschedule_mobile'] and b.get('test_name') == session['lab_reschedule_test'] and b.get('date') == session['lab_reschedule_old_date'] and b.get('time') == session['lab_reschedule_old_time']:
                        b['date'] = new_date
                        b['time'] = new_time
                with open('lab_bookings.json', 'w', encoding='utf-8') as f:
                    json.dump(bookings, f, indent=2)
            except Exception as e:
                logger.error(f"Error updating lab_bookings.json: {e}")
            # Step 6: Confirm and send SMS
            sms_msg = (
                f"Your lab test booking has been rescheduled!\n"
                f"Test: {session['lab_reschedule_test']}\n"
                f"Date: {new_date}\n"
                f"Time: {new_time}\n"
                f"Name: {session['lab_reschedule_name']}\n"
                f"Mobile: {session['lab_reschedule_mobile']}"
            )
            try:
                send_sms(f"+91{session['lab_reschedule_mobile']}", sms_msg)
            except Exception as e:
                logger.error(f"Error sending SMS: {e}")
            current_lang = session.get('language','english')
            speak_message(resp, f"आपका लैब टेस्ट {new_date} को {new_time} पर रीसिड्यूल हो गया है। धन्यवाद!" if current_lang=='hindi' else f"Your lab test has been rescheduled to {new_date} at {new_time}. Thank you!", current_lang)
            resp.hangup()
            return str(resp)
        elif any(word in answer for word in no_words):
            current_lang = session.get('language','english')
            speak_message(resp, "ठीक है, रीसिड्यूल रद्द कर दिया गया है। आपका लैब टेस्ट बुकिंग अपरिवर्तित है। धन्यवाद!" if current_lang=='hindi' else "Okay, rescheduling cancelled. Your lab test booking remains unchanged. Thank you!", current_lang)
            resp.hangup()
            return str(resp)
        else:
            current_lang = session.get('language','english')
            gather = Gather(input='speech', action='/reschedule-lab-test', method='POST', timeout=12, language=LANGUAGE_OPTIONS[current_lang]['language_code'])
            speak_message(gather, "क्या नई तारीख और समय सही है? कृपया हाँ या ना कहें।" if current_lang=='hindi' else "Is the new date and time correct? Please say yes or no.", current_lang)
            resp.append(gather)
            return str(resp)
    else:
        current_lang = session.get('language','english')
        speak_message(resp, "क्षमा कीजिए, रीसिड्यूल प्रक्रिया में कुछ त्रुटि हो गई। कृपया पुनः प्रयास करें।" if current_lang=='hindi' else "Sorry, something went wrong in the rescheduling process. Please try again.", current_lang)
        resp.hangup()
        return str(resp)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return 'No file part', 400
    file = request.files['file']
    if file.filename == '':
        return 'No selected file', 400
    save_path = os.path.join('upload', file.filename)
    file.save(save_path)
    return 'File uploaded', 200

@app.route('/api/lab-booking', methods=['POST'])
def api_lab_booking():
    data = request.get_json()
    required = ['test_name', 'date', 'time', 'name', 'mobile', 'home_collection']
    if not all(k in data for k in required):
        return jsonify({"success": False, "message": "Missing required fields."}), 400
    try:
        # Check if slot is already booked (Postgres)
        if is_lab_slot_booked(data['test_name'], data['date'], data['time']):
            return jsonify({"success": False, "message": "Slot already booked."}), 409
        insert_lab_booking(
            data['test_name'],
            data['date'],
            data['time'],
            data['name'],
            data['mobile'],
            data['home_collection']
        )
        return jsonify({"success": True, "message": "Lab test booked successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/doctor-booking', methods=['POST'])
def api_doctor_booking():
    data = request.get_json()
    required = ['department', 'doctor', 'date', 'time', 'name', 'mobile']
    if not all(k in data for k in required):
        return jsonify({"success": False, "message": "Missing required fields."}), 400
    try:
        # Check if slot is already booked (Postgres)
        if is_slot_booked(data['doctor'], data['date'], data['time']):
            return jsonify({"success": False, "message": "Slot already booked."}), 409
        insert_booking(
            data['department'],
            data['doctor'],
            data['date'],
            data['time'],
            data['name'],
            data['mobile']
        )
        return jsonify({"success": True, "message": "Doctor appointment booked successfully."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/lab-bookings', methods=['GET'])
def api_get_lab_bookings():
    try:
        conn = get_lab_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, test_name, date, time, name, mobile, home_collection, created_at FROM lab_bookings ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        bookings = [
            {
                "id": row[0],
                "test_name": row[1],
                "date": str(row[2]),
                "time": row[3],
                "name": row[4],
                "mobile": row[5],
                "home_collection": row[6],
                "created_at": row[7].isoformat() if row[7] else None
            }
            for row in rows
        ]
        return jsonify({"success": True, "bookings": bookings})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/doctor-bookings', methods=['GET'])
def api_get_doctor_bookings():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, department, doctor, date, time, name, mobile FROM bookings ORDER BY date DESC, time DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        bookings = [
            {
                "id": row[0],
                "department": row[1],
                "doctor": row[2],
                "date": str(row[3]),
                "time": row[4],
                "name": row[5],
                "mobile": row[6]
            }
            for row in rows
        ]
        return jsonify({"success": True, "bookings": bookings})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/upload-csv', methods=['POST'])
def upload_csv():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No selected file'}), 400
    save_path = os.path.join('upload_csv', file.filename)
    file.save(save_path)
    # Convert to JSON
    hospital_name = file.filename.rsplit('.', 1)[0]
    json_path = os.path.join('upload', f"{hospital_name}.json")
    data = []
    try:
        import csv, json
        with open(save_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                data.append(row)
        with open(json_path, 'w', encoding='utf-8') as jsonfile:
            json.dump(data, jsonfile, indent=2)
        return jsonify({'success': True, 'message': f'File {file.filename} uploaded and converted to {hospital_name}.json.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

def extract_time(text, lang='english'):
    """Enhanced time extraction with Hindi support"""
    if lang == 'hindi':
        # Convert Hindi numbers to English
        text = convert_hindi_numbers(text)
        
        # Handle Hindi time patterns
        hindi_patterns = {
            'सुबह': 'morning',
            'दोपहर': 'afternoon', 
            'शाम': 'evening',
            'रात': 'night',
            'बजे': 'o\'clock',
            'सुबह के': 'morning',
            'शाम के': 'evening',
            'दोपहर के': 'afternoon'
        }
        
        for hindi_term, english_term in hindi_patterns.items():
            text = text.replace(hindi_term, english_term)
    
        # Convert Hindi time expressions to specific times
        if 'morning' in text:
            text = text.replace('morning', '9:00 AM')
        elif 'afternoon' in text:
            text = text.replace('afternoon', '2:00 PM')
        elif 'evening' in text:
            text = text.replace('evening', '6:00 PM')
        elif 'night' in text:
            text = text.replace('night', '8:00 PM')
    
    # Enhanced time extraction logic
    time_match = re.search(r'(\d{1,2}:\d{2}|\d{1,2} ?[ap]m)', text.lower())
    if time_match:
        time_str = time_match.group(1)
        # Try to parse with dateparser for better handling
        try:
            parsed = dateparser.parse(time_str)
            if parsed:
                return parsed.strftime('%H:%M')
        except:
            pass
        return time_str
    return None

def is_yes(text, lang='english'):
    """Enhanced yes detection with Hindi support"""
    yes_words = {
        'english': ['yes', 'yeah', 'yup', 'yep', 'correct', 'right', 'ya', 'sure', 'ok', 'okay'],
        'hindi': ['हाँ', 'हां', 'ठीक', 'सही', 'जी', 'जी हाँ', 'बिल्कुल', 'ओके']
    }
    return any(word in text.lower() for word in yes_words.get(lang, yes_words['english']))

@app.route('/status', methods=['POST'])
def status():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })

if __name__ == "__main__":
    # Register blueprints
    app.register_blueprint(bp_doctor, url_prefix='/doctor')
    app.register_blueprint(bp_lab, url_prefix='/lab')
    
    # Start the Flask server
    print("🚀 Starting Flask server...")
    print("📱 Twilio webhook endpoints available at:")
    print("   - /voice (POST) - Voice call handling")
    print("   - /sms (POST) - SMS handling")
    print("   - /status (POST) - Health check")
    print("🌐 Server will be available at: http://localhost:5000")
    
    app.run(host='0.0.0.0', port=5000, debug=True)

 