"""
Hospital RAG System with LangChain
==================================

This module provides a comprehensive conversational RAG system for hospital services,
including appointment booking, lab test booking, and general hospital information.

Features:
- Memory management with conversation history
- Intent classification for different hospital services
- RAG-based responses using hospital documents
- Integration with existing hospital data (doctors, departments, lab tests)
- Multi-language support (English and Hindi)

Note: Give answer in language of the question, and keep it concise.
"""

import os
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

# LangChain imports
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.memory import ConversationBufferMemory
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableBranch
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

# Other libraries
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import langchain
from langchain_community.cache import InMemoryCache

# Set up LangChain cache
langchain.llm_cache = InMemoryCache()

# Environment setup
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or 'AIzaSyAF4fYr8v1Hbu884zJWtxw_xm1MHGLlgiQ'
os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY


class HospitalMemoryManager:
    """Advanced memory management system for hospital conversations."""

    def __init__(self, persistence_file: str = "hospital_memory.json"):
        self.short_term_memory = ConversationBufferMemory(k=5)
        self.long_term_memory = []
        self.episodic_memory = {}  # Key: session_id, Value: list of conversations
        self.current_session = str(uuid.uuid4())
        self.persistence_file = persistence_file
        self.episodic_memory[self.current_session] = []
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        self.load_memory()

    def add_conversation(self, user_input: str, ai_response: str, intent: str = None):
        """Add a conversation to all memory systems."""
        timestamp = datetime.now().isoformat()
        conversation = {
            "user_input": user_input,
            "ai_response": ai_response,
            "intent": intent,
            "timestamp": timestamp
        }
        self.short_term_memory.save_context({"input": user_input}, {"output": ai_response})
        if not self._is_duplicated(conversation):
            self.long_term_memory.append(conversation)
            self.episodic_memory[self.current_session].append(conversation)
            if len(self.long_term_memory) > 50:
                self.long_term_memory.pop(0)
        self.save_memory()

    def _is_duplicated(self, new_conversation: Dict) -> bool:
        """Check for similarity to avoid storing redundant information."""
        if not self.long_term_memory:
            return False
        recent_conversations = self.long_term_memory[-5:]
        new_text = f"{new_conversation['user_input']} {new_conversation['ai_response']}"
        new_embedding = self.embedding_model.encode(new_text)
        for conv in recent_conversations:
            existing_text = f"{conv['user_input']} {conv['ai_response']}"
            existing_embedding = self.embedding_model.encode(existing_text)
            if cosine_similarity([new_embedding], [existing_embedding])[0][0] > 0.95:
                return True
        return False

    def get_context(self, query: str, num_conversations: int = 3) -> str:
        """Retrieve relevant conversation history for context."""
        if not self.long_term_memory:
            return "No recent conversation history."
        recent = self.episodic_memory.get(self.current_session, [])[-num_conversations:]
        context_str = "Recent conversation history:\n"
        for conv in recent:
            context_str += f"User: {conv['user_input']}\nAI: {conv['ai_response']}\n"
        return context_str

    def save_memory(self):
        """Save memory to a JSON file."""
        memory_data = {
            "long_term_memory": self.long_term_memory,
            "episodic_memory": self.episodic_memory,
            "current_session": self.current_session
        }
        with open(self.persistence_file, 'w') as f:
            json.dump(memory_data, f, indent=2)

    def load_memory(self):
        """Load memory from a JSON file."""
        if os.path.exists(self.persistence_file):
            with open(self.persistence_file, 'r') as f:
                memory_data = json.load(f)
            self.long_term_memory = memory_data.get("long_term_memory", [])
            self.episodic_memory = memory_data.get("episodic_memory", {})
            self.current_session = memory_data.get("current_session", str(uuid.uuid4()))
            if self.current_session not in self.episodic_memory:
                self.episodic_memory[self.current_session] = []

    def start_new_session(self):
        """Start a new conversation session."""
        self.current_session = str(uuid.uuid4())
        self.episodic_memory[self.current_session] = []
        self.short_term_memory.clear()
        self.save_memory()
        print("--- New Hospital Session Started ---")

    def get_memory_stats(self) -> Dict:
        """Get statistics about memory usage."""
        return {
            "long_term_memory_count": len(self.long_term_memory),
            "episodic_memory_sessions": len(self.episodic_memory),
            "current_session_conversations": len(self.episodic_memory.get(self.current_session, [])),
        }


class HospitalRAGChain:
    """Conversational RAG system for hospital services."""

    def __init__(self, hospital_data_dir: str = "."):
        self.memory_manager = HospitalMemoryManager()
        self.model = ChatGoogleGenerativeAI(model='gemini-2.5-flash', temperature=0.7)
        self.embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
        self.parser = StrOutputParser()
        self.hospital_data_dir = hospital_data_dir

        # Load hospital data
        self.hospital_data = self._load_hospital_data()
        
        # Initialize vector store if PDF exists
        self.retriever = None
        self._initialize_vector_store()

        # Define component chains
        self._setup_chains()

    def _load_hospital_data(self) -> Dict:
        """Load hospital data from JSON files."""
        data = {}
        
        # Load doctors list
        doctors_file = os.path.join(self.hospital_data_dir, "doctors_list.json")
        if os.path.exists(doctors_file):
            with open(doctors_file, 'r') as f:
                doctors_data = json.load(f)
                data['doctors'] = doctors_data.get('doctors', [])
        
        # Load lab tests
        lab_tests_file = os.path.join(self.hospital_data_dir, "lab_tests.json")
        if os.path.exists(lab_tests_file):
            with open(lab_tests_file, 'r') as f:
                lab_data = json.load(f)
                data['lab_tests'] = lab_data.get('lab_tests', [])
        
        # Load appointments
        appointments_file = os.path.join(self.hospital_data_dir, "appointments.json")
        if os.path.exists(appointments_file):
            with open(appointments_file, 'r') as f:
                appointments_data = json.load(f)
                data['appointments'] = appointments_data.get('appointments', [])
        
        return data

    def _initialize_vector_store(self):
        """Initialize vector store from PDF documents."""
        pdf_path = os.path.join(self.hospital_data_dir, "D:\RAG_hospital\RAG\shalby_main.pdf")
        if os.path.exists(pdf_path):
            try:
                print("Loading and processing hospital PDF...")
                loader = PyPDFLoader(pdf_path)
                docs = loader.load_and_split(
                    text_splitter=RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=200)
                )
                vectorstore = FAISS.from_documents(docs, self.embeddings)
                self.retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 3})
                print("Hospital PDF processed successfully.")
            except Exception as e:
                print(f"Error processing PDF: {e}")
                self.retriever = None
        else:
            print("No hospital_info.pdf found. Using only structured data.")

    def _setup_chains(self):
        """Set up the component chains."""
        
        # 1. Intent Classifier
        classifier_prompt = PromptTemplate(
            template="""Classify the user's intent into one of these categories:
- appointment: Questions about booking, rescheduling, or managing doctor appointments
- lab_test: Questions about lab tests, blood tests, or diagnostic procedures
- general: General hospital information, facilities, or other queries

User input: {input}
Intent:""",
            input_variables=['input'],
        )
        self.classifier_chain = classifier_prompt | self.model | self.parser

        # 2. Appointment Chain
        appointment_prompt = PromptTemplate(
            template="""You are a helpful hospital appointment assistant. Answer questions about appointments using the provided hospital data.

HOSPITAL DATA:
{hospital_data}

CONVERSATION HISTORY:
{chat_history}

USER QUESTION: {input}

Provide helpful, accurate information about appointments. If specific data is not available, provide general guidance.
Answer in the same language as the question.

ANSWER:""",
            input_variables=['hospital_data', 'chat_history', 'input'],
        )
        self.appointment_chain = appointment_prompt | self.model | self.parser

        # 3. Lab Test Chain
        lab_prompt = PromptTemplate(
            template="""You are a helpful hospital lab test assistant. Answer questions about lab tests using the provided data.

LAB TEST DATA:
{lab_data}

CONVERSATION HISTORY:
{chat_history}

USER QUESTION: {input}

Provide helpful, accurate information about lab tests. If specific data is not available, provide general guidance.
Answer in the same language as the question.

ANSWER:""",
            input_variables=['lab_data', 'chat_history', 'input'],
        )
        self.lab_chain = lab_prompt | self.model | self.parser

        # 4. General Chain (with RAG if available)
        general_prompt = PromptTemplate(
            template="""You are a helpful hospital information assistant. Answer general questions about the hospital.

            Note: Give answer in language of the question, and keep it concise.

HOSPITAL DATA:
{hospital_data}

CONTEXT FROM DOCUMENTS:
{context}

CONVERSATION HISTORY:
{chat_history}

USER QUESTION: {input}

Provide helpful, accurate information about the hospital. If specific data is not available, provide general guidance.
Answer in the same language as the question.

ANSWER:""",
            input_variables=['hospital_data', 'context', 'chat_history', 'input'],
        )
        
        if self.retriever:
            general_chain = (
                RunnablePassthrough.assign(
                    context=lambda x: self._format_docs(self.retriever.invoke(x["input"]))
                ) | general_prompt | self.model | self.parser
            )
        else:
            general_chain = general_prompt | self.model | self.parser
        
        self.general_chain = general_chain

        # 5. Branch router
        self.branch = RunnableBranch(
            (lambda x: "appointment" in x.get("intent", "").lower(), self.appointment_chain),
            (lambda x: "lab_test" in x.get("intent", "").lower(), self.lab_chain),
            self.general_chain,
        )

    def _format_docs(self, docs: List[Document]) -> str:
        """Format documents for context."""
        return "\n\n".join(d.page_content for d in docs)

    def _format_hospital_data(self) -> str:
        """Format hospital data for context."""
        data_str = ""
        
        if 'doctors' in self.hospital_data:
            data_str += "AVAILABLE DOCTORS:\n"
            for doctor in self.hospital_data['doctors']:
                name = doctor.get('doctor_name', 'Unknown')
                department = doctor.get('doctor_department', 'Unknown')
                experience = doctor.get('experience', 'Unknown')
                timing = doctor.get('doctor_available_time', 'Unknown')
                data_str += f"- {name} ({department}) - Experience: {experience}, Timing: {timing}\n"
            data_str += "\n"
        
        if 'lab_tests' in self.hospital_data:
            data_str += "AVAILABLE LAB TESTS:\n"
            for test in self.hospital_data['lab_tests']:
                name = test.get('test_name', 'Unknown')
                description = test.get('description', 'No description')
                price = test.get('price', 'Price not available')
                data_str += f"- {name}: {description} (Price: {price})\n"
            data_str += "\n"
        
        return data_str

    def invoke(self, user_input: str) -> str:
        """Process user input and return response."""
        # Get conversation history
        chat_history = self.memory_manager.get_context(user_input)

        # Classify intent
        intent = self.classifier_chain.invoke({"input": user_input})

        # Prepare input for the appropriate chain
        if "appointment" in intent.lower():
            chain_input = {
                "input": user_input,
                "chat_history": chat_history,
                "hospital_data": self._format_hospital_data(),
                "intent": intent
            }
            print("\n[INFO] Routing to: Appointment Chain")
        elif "lab_test" in intent.lower():
            lab_data = json.dumps(self.hospital_data.get('lab_tests', []), indent=2)
            chain_input = {
                "input": user_input,
                "chat_history": chat_history,
                "lab_data": lab_data,
                "intent": intent
            }
            print("\n[INFO] Routing to: Lab Test Chain")
        else:
            chain_input = {
                "input": user_input,
                "chat_history": chat_history,
                "hospital_data": self._format_hospital_data(),
                "context": self._format_docs(self.retriever.invoke(user_input)) if self.retriever else "",
                "intent": intent
            }
            print("\n[INFO] Routing to: General Chain")

        # Get response
        response = self.branch.invoke(chain_input)

        # Save to memory
        self.memory_manager.add_conversation(user_input, response, intent)

        return response

    def get_available_doctors(self, department: str = None) -> List[Dict]:
        """Get available doctors, optionally filtered by department."""
        if 'doctors' not in self.hospital_data:
            return []
        
        doctors = self.hospital_data['doctors']
        if department:
            return [d for d in doctors if d.get('department', '').lower() == department.lower()]
        return doctors

    def get_available_lab_tests(self) -> List[Dict]:
        """Get available lab tests."""
        return self.hospital_data.get('lab_tests', [])

    def get_memory_stats(self) -> Dict:
        """Get memory statistics."""
        return self.memory_manager.get_memory_stats()


# Convenience functions
def create_hospital_rag(hospital_data_dir: str = ".") -> HospitalRAGChain:
    """Create a new Hospital RAG Chain instance."""
    return HospitalRAGChain(hospital_data_dir)


def chat_with_hospital_rag(rag_chain: HospitalRAGChain):
    """Interactive chat interface for hospital RAG system."""
    print("\n🏥 Hospital RAG Assistant")
    print("=" * 50)
    print("Type 'quit' to exit, 'new' for new session, 'stats' for memory stats")
    print("-" * 50)

    while True:
        user_query = input("You: ")
        if user_query.lower() in ['quit', 'exit']:
            break
        elif user_query.lower() == 'new':
            rag_chain.memory_manager.start_new_session()
            continue
        elif user_query.lower() == 'stats':
            stats = rag_chain.get_memory_stats()
            print(f"Memory Stats: {json.dumps(stats, indent=2)}")
            continue
        
        try:
            ai_response = rag_chain.invoke(user_query)
            print(f"AI: {ai_response}\n")
        except Exception as e:
            print(f"AI: I'm sorry, I encountered an error. Please try again. (Error: {e})\n")

    print("-" * 50)
    print("Final Memory Stats:")
    print(json.dumps(rag_chain.get_memory_stats(), indent=2))


if __name__ == "__main__":
    # Test the hospital RAG system
    try:
        rag_chain = create_hospital_rag()
        chat_with_hospital_rag(rag_chain)
    except Exception as e:
        print(f"Error initializing hospital RAG: {e}")
        print("Please ensure your GOOGLE_API_KEY is set correctly.") 