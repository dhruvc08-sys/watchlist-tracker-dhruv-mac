import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Shared client: used to check who a token belongs to (get_user)
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

def new_client():
    # Fresh client for sign up / sign in
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

def get_user_client(access_token):
    # Fresh client that makes every database call as this specific user
    client = new_client()
    client.postgrest.auth(access_token)
    return client