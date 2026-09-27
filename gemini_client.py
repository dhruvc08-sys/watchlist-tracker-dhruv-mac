from google import genai
from google.genai import types, Client
from dotenv import load_dotenv
import os
from pydantic import BaseModel

load_dotenv()

gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"]

# Recommendation 

class Recommendation(BaseModel):
    movie_name: str
    movie_year: int
    why_it_fits: str

# Response 

class TasteProfile(BaseModel):
    headline: str
    taste: str
    recommendations: list[Recommendation]
