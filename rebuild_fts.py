import sqlite3

conn = sqlite3.connect(r'C:\Users\sahil\.claude\skills\play\tools\media_index.db')
cur = conn.cursor()

# 1. Rebuild FTS5 table
cur.execute("INSERT INTO fts_titles(fts_titles) VALUES('rebuild');")
conn.commit()

# 2. Check match
cur.execute("SELECT rowid, canonical_title FROM fts_titles WHERE fts_titles MATCH 'Aadujeevitham';")
fts_res = cur.fetchall()
print("FTS matches for Aadujeevitham:", fts_res)

# 3. Check releases
for row in fts_res:
    t_id = row[0]
    cur.execute("SELECT count(*) FROM releases WHERE title_id = ?", (t_id,))
    print(f"Releases for title ID {t_id}:", cur.fetchone()[0])

conn.close()
