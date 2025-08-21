"""
Test Integration: LangChain RAG + Google TTS + Hospital System
=============================================================

This script tests the integration of all components.
"""

import requests
import json
import time

def test_rag_integration():
    """Test the RAG integration endpoints"""
    
    base_url = "http://localhost:5000"
    
    print("🧪 Testing RAG Integration")
    print("=" * 50)
    
    # Test 1: Health Check
    print("\n1. Testing Health Check...")
    try:
        response = requests.get(f"{base_url}/health")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Health Check: {data['status']}")
            print(f"   - RAG System: {data.get('rag_system', 'unknown')}")
            print(f"   - TTS System: {data.get('tts_system', 'unknown')}")
            print(f"   - Active Sessions: {data.get('active_sessions', 0)}")
        else:
            print(f"❌ Health Check Failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Health Check Error: {e}")
    
    # Test 2: RAG Query
    print("\n2. Testing RAG Query...")
    try:
        test_query = "What doctors are available in cardiology?"
        response = requests.post(
            f"{base_url}/rag-query",
            json={"query": test_query},
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ RAG Query Success")
            print(f"   - Response: {data['response'][:100]}...")
            print(f"   - Audio Path: {data.get('audio_path', 'None')}")
        else:
            print(f"❌ RAG Query Failed: {response.status_code}")
            print(f"   - Error: {response.text}")
    except Exception as e:
        print(f"❌ RAG Query Error: {e}")
    
    # Test 3: Get Doctors
    print("\n3. Testing Get Doctors...")
    try:
        response = requests.get(f"{base_url}/rag-doctors")
        
        if response.status_code == 200:
            data = response.json()
            doctors = data.get('doctors', [])
            print(f"✅ Get Doctors Success: {len(doctors)} doctors found")
            
            # Show first 3 doctors
            for i, doctor in enumerate(doctors[:3]):
                name = doctor.get('doctor_name', 'Unknown')
                dept = doctor.get('doctor_department', 'Unknown')
                print(f"   - {name} ({dept})")
        else:
            print(f"❌ Get Doctors Failed: {response.status_code}")
            print(f"   - Error: {response.text}")
    except Exception as e:
        print(f"❌ Get Doctors Error: {e}")
    
    # Test 4: Get Doctors by Department
    print("\n4. Testing Get Doctors by Department...")
    try:
        response = requests.get(f"{base_url}/rag-doctors?department=Cardiac Sciences")
        
        if response.status_code == 200:
            data = response.json()
            doctors = data.get('doctors', [])
            print(f"✅ Get Doctors by Department Success: {len(doctors)} doctors found")
            
            for doctor in doctors:
                name = doctor.get('doctor_name', 'Unknown')
                dept = doctor.get('doctor_department', 'Unknown')
                print(f"   - {name} ({dept})")
        else:
            print(f"❌ Get Doctors by Department Failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Get Doctors by Department Error: {e}")
    
    # Test 5: RAG Stats
    print("\n5. Testing RAG Stats...")
    try:
        response = requests.get(f"{base_url}/rag-stats")
        
        if response.status_code == 200:
            data = response.json()
            stats = data.get('stats', {})
            print(f"✅ RAG Stats Success")
            print(f"   - Long-term Memory: {stats.get('long_term_memory_count', 0)} conversations")
            print(f"   - Episodic Memory Sessions: {stats.get('episodic_memory_sessions', 0)}")
            print(f"   - Current Session: {stats.get('current_session_conversations', 0)} conversations")
        else:
            print(f"❌ RAG Stats Failed: {response.status_code}")
    except Exception as e:
        print(f"❌ RAG Stats Error: {e}")

def test_multiple_queries():
    """Test multiple RAG queries to verify memory and responses"""
    
    base_url = "http://localhost:5000"
    
    print("\n🧪 Testing Multiple Queries (Memory Test)")
    print("=" * 50)
    
    test_queries = [
        "Hello, how can you help me?",
        "What doctors do you have?",
        "Tell me about cardiology department",
        "How do I book an appointment?",
        "What are your hospital hours?",
        "नमस्ते, क्या आपके पास कार्डियोलॉजी विभाग है?"
    ]
    
    for i, query in enumerate(test_queries, 1):
        print(f"\nQuery {i}: {query}")
        try:
            response = requests.post(
                f"{base_url}/rag-query",
                json={"query": query},
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Response: {data['response'][:80]}...")
                if data.get('audio_path'):
                    print(f"   🔊 Audio generated: {data['audio_path']}")
            else:
                print(f"❌ Failed: {response.status_code}")
            
            # Small delay between queries
            time.sleep(1)
            
        except Exception as e:
            print(f"❌ Error: {e}")

def test_tts_functionality():
    """Test TTS functionality specifically"""
    
    base_url = "http://localhost:5000"
    
    print("\n🔊 Testing TTS Functionality")
    print("=" * 50)
    
    # Test with a simple query that should generate audio
    test_query = "Hello, this is a test of the text-to-speech system."
    
    try:
        response = requests.post(
            f"{base_url}/rag-query",
            json={"query": test_query},
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ TTS Test Success")
            print(f"   - Response: {data['response']}")
            
            if data.get('audio_path'):
                print(f"   🔊 Audio Path: {data['audio_path']}")
                print(f"   📁 Audio file should be available at: {data['audio_path']}")
            else:
                print(f"   ⚠️  No audio path returned (TTS might be unavailable)")
        else:
            print(f"❌ TTS Test Failed: {response.status_code}")
            
    except Exception as e:
        print(f"❌ TTS Test Error: {e}")

if __name__ == "__main__":
    print("🏥 Testing Integrated Hospital System")
    print("=" * 60)
    print("Make sure your server is running on http://localhost:5000")
    print("=" * 60)
    
    # Test basic integration
    test_rag_integration()
    
    # Test multiple queries
    test_multiple_queries()
    
    # Test TTS functionality
    test_tts_functionality()
    
    print("\n🎉 Integration Testing Complete!")
    print("\nNext Steps:")
    print("1. Test voice calls through Twilio")
    print("2. Check audio files in static/audio_cache/")
    print("3. Monitor logs for any errors")
    print("4. Use /health endpoint to monitor system status") 