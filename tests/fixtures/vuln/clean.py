"""A clean fixture — the enumerator must find NOTHING here. Not imported/run."""
import os
import sqlite3

API_KEY = os.environ["API_KEY"]          # reference, not a literal → not a secret
db_password = os.getenv("DB_PASSWORD")   # reference, not a literal


def lookup(cur: sqlite3.Cursor, user_id):
    # parameterised query — the safe form, must not trip sql-injection
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))


def greet(name: str) -> str:
    return f"hello {name}"          # an f-string, but no execute() → not SQL
