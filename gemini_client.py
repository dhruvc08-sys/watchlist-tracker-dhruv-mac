from google import genai
from dotenv import load_dotenv
import os
from pydantic import BaseModel

load_dotenv()

gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
# Tried in order: if the first model is overloaded, main.py falls back to the next
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"]

# Recommendation: the shape of one recommended movie in Gemini's JSON reply

class Recommendation(BaseModel):
    movie_name: str
    movie_year: int
    why_it_fits: str

# Response: the full JSON Gemini must return (passed as response_schema in main.py)

class TasteProfile(BaseModel):
    headline: str
    taste: str
    recommendations: list[Recommendation]
