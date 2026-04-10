"""
gmail-module/store.py

Email vector store — own tables in chatbot/elvis.db, separate from the
main vec_items/vec_metadata tables so the CHECK constraint isn't affected.

Tables:
  email_vec_items    — sqlite-vec virtual table (768-dim embeddings)
  email_vec_metadata — message metadata + body snippet
"""

import sqlite3
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

import ollama
import sqlite_vec

from config import DB_PATH, OLLAMA_BASE_URL, EMBED_MODEL, EMBED_DIM


@dataclass
class EmailRecord:
    message_id: str
    subject: str
    sender: str
    date: str
    snippet: str
    body: str


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_email_tables(db_path: str = DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS email_vec_items USING vec0(
                embedding float[{EMBED_DIM}]
            );

            CREATE TABLE IF NOT EXISTS email_vec_metadata (
                rowid      INTEGER PRIMARY KEY,
                message_id TEXT NOT NULL UNIQUE,
                subject    TEXT NOT NULL DEFAULT '',
                sender     TEXT NOT NULL DEFAULT '',
                date       TEXT NOT NULL DEFAULT '',
                snippet    TEXT NOT NULL DEFAULT '',
                body       TEXT NOT NULL DEFAULT ''
            );
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed(text: str) -> List[float]:
    client = ollama.Client(host=OLLAMA_BASE_URL)
    resp = client.embed(model=EMBED_MODEL, input=text)
    return resp.embeddings[0]


def _pack(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

def clear_emails(db_path: str = DB_PATH):
    """Delete all stored emails (metadata + vectors)."""
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("DELETE FROM email_vec_metadata")
        conn.execute("DELETE FROM email_vec_items")
        conn.commit()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_email(email: EmailRecord, db_path: str = DB_PATH) -> bool:
    """Embed and upsert a single email. Returns True on success."""
    embed_text = f"Subject: {email.subject}\nFrom: {email.sender}\n\n{email.body or email.snippet}"
    try:
        vec = _embed(embed_text)
    except Exception as e:
        print(f"[EmailStore] Embedding failed for {email.message_id}: {e}")
        return False

    packed = _pack(vec)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        row = conn.execute(
            "SELECT rowid FROM email_vec_metadata WHERE message_id = ?",
            (email.message_id,),
        ).fetchone()

        if row:
            rowid = row[0]
            conn.execute(
                "UPDATE email_vec_metadata SET subject=?, sender=?, date=?, snippet=?, body=? WHERE rowid=?",
                (email.subject, email.sender, email.date, email.snippet, email.body, rowid),
            )
            conn.execute("DELETE FROM email_vec_items WHERE rowid=?", (rowid,))
            conn.execute("INSERT INTO email_vec_items(rowid, embedding) VALUES (?, ?)", (rowid, packed))
        else:
            cur = conn.execute(
                "INSERT INTO email_vec_metadata (message_id, subject, sender, date, snippet, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (email.message_id, email.subject, email.sender, email.date, email.snippet, email.body),
            )
            conn.execute(
                "INSERT INTO email_vec_items(rowid, embedding) VALUES (?, ?)",
                (cur.lastrowid, packed),
            )

        conn.commit()

    print(f"[EmailStore] Stored: {email.subject[:60]}")
    return True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def search_emails(query: str, top_k: int = 5, db_path: str = DB_PATH) -> List[EmailRecord]:
    """Semantic search over stored emails. Returns closest matches."""
    try:
        vec = _embed(query)
    except Exception as e:
        print(f"[EmailStore] Query embedding failed: {e}")
        return []

    packed = _pack(vec)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute("""
            SELECT m.message_id, m.subject, m.sender, m.date, m.snippet, m.body, v.distance
            FROM email_vec_items v
            JOIN email_vec_metadata m ON v.rowid = m.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """, (packed, top_k)).fetchall()

    return [
        EmailRecord(
            message_id=r[0], subject=r[1], sender=r[2],
            date=r[3], snippet=r[4], body=r[5],
        )
        for r in rows
    ]


def list_emails(db_path: str = DB_PATH) -> List[EmailRecord]:
    """Return all stored emails ordered by date descending."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT message_id, subject, sender, date, snippet, body "
            "FROM email_vec_metadata ORDER BY date DESC"
        ).fetchall()
    return [EmailRecord(*r) for r in rows]


def count_emails(db_path: str = DB_PATH) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM email_vec_metadata").fetchone()[0]
