#!/bin/bash
echo "Setting up FastAPI project..."
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
echo "Add SUPABASE_URL and SUPABASE_KEY to .env."
echo "Run schema.sql in the Supabase SQL editor, then: python -m uvicorn main:app --reload"
