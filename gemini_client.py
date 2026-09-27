from google import genai
from google.genai import types, Client
from dotenv import load_dotenv
import os

load_dotenv()

gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-3.8-flash"

