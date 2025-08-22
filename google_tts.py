from google.cloud import texttospeech
import os
import hashlib
import json

class GoogleCloudTTS:
    def __init__(self, cache_dir="audio_cache"):
        """Initialize Google Cloud TTS client with caching"""
        self.client = None
        self.cache_dir = cache_dir
        
        # Create cache directory if it doesn't exist
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
            
        # Create cache index file if it doesn't exist
        self.cache_index_path = os.path.join(cache_dir, 'cache_index.json')
        if not os.path.exists(self.cache_index_path):
            with open(self.cache_index_path, 'w') as f:
                json.dump({}, f)
        
        # Try to initialize Google Cloud client
        try:
            self.client = texttospeech.TextToSpeechClient()
            print("✅ Google Cloud TTS client initialized successfully")
        except Exception as e:
            print(f"⚠️ Warning: Could not initialize Google Cloud TTS: {e}")
            print("   This could be due to:")
            print("   - Missing or invalid service account credentials")
            print("   - Text-to-Speech API not enabled")
            print("   - Billing not enabled for the project")
            print("   - Insufficient permissions")
            self.client = None

    def _get_cache_key(self, text, voice_name, language_code):
        """Generate a unique cache key based on input parameters"""
        params = f"{text}_{voice_name}_{language_code}"
        return hashlib.md5(params.encode()).hexdigest()

    def _get_cached_audio(self, cache_key):
        """Try to get audio from cache"""
        try:
            with open(self.cache_index_path, 'r') as f:
                cache_index = json.load(f)
            
            if cache_key in cache_index:
                audio_path = os.path.join(self.cache_dir, cache_index[cache_key])
                if os.path.exists(audio_path):
                    return audio_path
        except Exception as e:
            print(f"Cache read error: {e}")
        return None

    def _save_to_cache(self, cache_key, audio_content):
        """Save audio to cache"""
        try:
            audio_filename = f"{cache_key}.mp3"
            audio_path = os.path.join(self.cache_dir, audio_filename)
            
            with open(audio_path, 'wb') as f:
                f.write(audio_content)
                
            with open(self.cache_index_path, 'r') as f:
                cache_index = json.load(f)
            
            cache_index[cache_key] = audio_filename
            
            with open(self.cache_index_path, 'w') as f:
                json.dump(cache_index, f)
                
            return audio_path
        except Exception as e:
            print(f"Cache write error: {e}")
            return None

    def generate_speech(self, text, voice_name='en-IN-Standard-A', language_code='en-IN', speaking_rate=1.0, pitch=0.0, volume_gain_db=0.0):
        """
        Generate speech from text using Google Cloud TTS
        Returns the path to the audio file (either cached or newly generated)
        """
        try:
            # Check cache first
            cache_key = self._get_cache_key(text, voice_name, language_code)
            cached_path = self._get_cached_audio(cache_key)
            if cached_path:
                return cached_path

            # If Google Cloud TTS is not available, return None
            if not self.client:
                print("❌ Google Cloud TTS not available. Please check your credentials and billing setup.")
                return None

            # Set up the synthesis input
            synthesis_input = texttospeech.SynthesisInput(text=text)

            # Build voice params
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name
            )

            # Select the audio encoding with speaking rate, pitch, and volume
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=speaking_rate,
                pitch=pitch,
                volume_gain_db=volume_gain_db
            )

            # Perform the text-to-speech request
            response = self.client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )

            # Save to cache and return path
            return self._save_to_cache(cache_key, response.audio_content)

        except Exception as e:
            print(f"Speech generation error: {e}")
            if "401" in str(e) or "authentication" in str(e).lower():
                print("🔐 Authentication failed. Please check your Google Cloud credentials and billing setup.")
            elif "403" in str(e):
                print("🚫 Access denied. Please check if Text-to-Speech API is enabled and you have sufficient permissions.")
            return None