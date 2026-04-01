"""
elvis/documents.py

Document CRUD operations scoped tightly to the sandbox directory.
"""

import os
import io
import pandas as pd
from datetime import datetime
from core.config import DOCUMENTS_DIR

# Ensure sandbox directory exists
os.makedirs(DOCUMENTS_DIR, exist_ok=True)

MAX_FILE_CHARS = 12000

def _safe_path(filename: str) -> str:
    """
    Resolve and validate a path to ensure it remains strictly inside
    the defined DOCUMENTS_DIR.
    """
    # Create absolute path for the requested file and the base folder
    base_dir = os.path.abspath(DOCUMENTS_DIR)
    
    # We strip any leading/trailing whitespace and resolve the full path
    # If the user passes '../config.py', os.path.join resolves it relative to base_dir
    target_path = os.path.abspath(os.path.join(base_dir, filename))
    
    # Check if the target is exactly the base_dir or a child of it
    if not target_path.startswith(base_dir + os.sep) and target_path != base_dir:
        raise ValueError(f"Path outside of sandbox directory is forbidden: {filename}")
        
    return target_path

def list_documents_logic() -> str:
    """Recursively list documents in the sandbox directory."""
    base_dir = os.path.abspath(DOCUMENTS_DIR)
    results = []
    
    for root, _, files in os.walk(base_dir):
        for name in files:
            # Hide dotted/hidden files
            if name.startswith('.'):
                continue
                
            path = os.path.join(root, name)
            rel_path = os.path.relpath(path, base_dir)
            size = os.path.getsize(path)
            mod_time = os.path.getmtime(path)
            mod_date = datetime.fromtimestamp(mod_time).strftime("%Y-%m-%d %H:%M:%S")
            results.append(f"{rel_path} ({size} bytes, modified: {mod_date})")
            
    if not results:
        return "No documents found."
        
    return "\n".join(results)

def read_document_logic(filename: str) -> str:
    """Read file content safely. Uses pandas for CSV."""
    try:
        path = _safe_path(filename)
        
        if not os.path.exists(path):
            return f"File not found: {filename}"
        if not os.path.isfile(path):
            return f"Not a file: {filename}"
            
        ext = os.path.splitext(path)[1].lower()
        
        if ext == ".csv":
            try:
                df = pd.read_csv(path)
                content = df.to_markdown(index=False)
            except Exception as e:
                # Fallback to plain text if CSV is malformed
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                
        if len(content) > MAX_FILE_CHARS:
            return content[:MAX_FILE_CHARS] + "\n\n[truncated]"
        return content
        
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to read file: {str(e)}"

def write_document_logic(filename: str, content: str) -> str:
    """Write string to a file safely."""
    try:
        path = _safe_path(filename)
        
        # Ensure parent subdirectories exist if any
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
            
        return f"Successfully saved to {filename}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to write file: {str(e)}"

def delete_document_logic(filename: str) -> str:
    """Delete a file safely."""
    try:
        path = _safe_path(filename)
        if not os.path.exists(path):
            return f"File not found: {filename}"
            
        os.remove(path)
        return f"Successfully deleted {filename}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to delete file: {str(e)}"

def move_document_logic(old_name: str, new_name: str) -> str:
    """Rename/move a file safely."""
    try:
        old_path = _safe_path(old_name)
        new_path = _safe_path(new_name)
        
        if not os.path.exists(old_path):
            return f"Source file not found: {old_name}"
            
        # Ensure target's parent directory exists
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        
        os.rename(old_path, new_path)
        return f"Successfully moved {old_name} to {new_name}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Failed to move file: {str(e)}"
