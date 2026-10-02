import sqlite3

conn = sqlite3.connect(r'D:\play skill\media_index.db')
cur = conn.cursor()

titles = [
    'Thondimuthalum Driksakshiyum',
    'Kummatty',
    'Ustad Hotel',
    'Andhadhun',
    'Tumbbad',
    'Memories of Murder',
    'Perfect Blue',
    'Ergo Proxy',
    'The Fall',
    'Coherence'
]

print("=== VERIFYING CATALOG INGESTION IN D:\\play skill\\media_index.db ===")
for t in titles:
    cur.execute("""
        SELECT t.id, t.canonical_title, count(r.id)
        FROM titles t
        LEFT JOIN releases r ON t.id = r.title_id
        WHERE t.canonical_title LIKE ?
        GROUP BY t.id
    """, (f'%{t}%',))
    rows = cur.fetchall()
    print(f"{t:<30} -> {rows}")

cur.execute("INSERT INTO fts_titles(fts_titles) VALUES('rebuild');")
conn.commit()
conn.close()
print("\n[+] Full FTS5 Trigram table rebuilt successfully.")
