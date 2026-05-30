import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'registry.sqlite')

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            hash TEXT NOT NULL,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def register_source(path, file_hash):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO sources (path, hash) VALUES (?, ?)', (path, file_hash))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Already ingested or path exists.
        return False
    finally:
        conn.close()
