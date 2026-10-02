import sqlite3

conn = sqlite3.connect(r'C:\Users\sahil\.claude\skills\play\tools\media_index.db')
cur = conn.cursor()

# Remove stale duplicates
cur.execute("DELETE FROM titles WHERE id IN (9761, 9765);")
cur.execute("INSERT INTO fts_titles(fts_titles) VALUES('rebuild');")
conn.commit()

# Verify search
cur.execute("SELECT id, canonical_title FROM titles WHERE canonical_title LIKE '%Aadujeevitham%';")
print("Remaining Aadujeevitham entries:", cur.fetchall())

conn.close()
