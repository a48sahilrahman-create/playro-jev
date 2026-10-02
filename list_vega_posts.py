import sqlite3, os

db_path = os.path.expanduser(r'~\.claude\skills\play\tools\media_index.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT t.id, t.canonical_title, r.target_url FROM titles t JOIN releases r ON t.id = r.title_id WHERE r.platform = 'VegaMovies' LIMIT 40")
for row in cur.fetchall():
    print(f"ID: {row[0]} | Title: {row[1][:40]} | URL: {row[2]}")

conn.close()
