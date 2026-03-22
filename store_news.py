from elvis.utils.get_news import store_news
from elvis.data import create_db_connection

conn = create_db_connection()

count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
confirm = input(f"Delete all {count} memories? (y/n): ").strip().lower()

if confirm == "y":
    conn.execute("DELETE FROM memories")
    conn.commit()
    print("All memories deleted.")
else:
    print("Aborted.")


store_news("https://feeds.bbci.co.uk/news/world/rss.xml")
store_news("https://feeds.bbci.co.uk/news/technology/rss.xml")