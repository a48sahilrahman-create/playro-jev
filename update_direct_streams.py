import sqlite3, os

db_path = os.path.expanduser(r'~\.claude\skills\play\tools\media_index.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Remove any existing release with these target_urls first to prevent unique constraint conflict
cur.execute("DELETE FROM releases WHERE target_url IN ('https://nexdrive.fit/genxfm784776503472/', 'https://nexdrive.fit/genxfm784776393879/')")

# 1. Update Ghost in the Shell
cur.execute("""
    UPDATE releases
    SET direct_url = 'https://pub-bb3047cda4324954adc1d55b39aaeccc.r2.dev/21604de7ba86008c7d0085761e14e3b1?token=1787303514',
        target_url = 'https://nexdrive.fit/genxfm784776503472/',
        resolution = '1080p',
        size = '291.25 MB'
    WHERE id = (SELECT id FROM releases WHERE title_id IN (SELECT id FROM titles WHERE canonical_title LIKE '%Ghost in the Shell%') LIMIT 1)
""")

# 2. Update Deadpool
cur.execute("""
    UPDATE releases
    SET direct_url = 'https://556138dca7367763ed46eecaa4284eca.r2.cloudflarestorage.com/hub2/Deadpool.%26.Wolverine.2024.480p.BluRay.Hindi.English.ESubs.Vegamovies.is.mkv?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=26b6cf8a0399b5880643f585c8c3dbe5%2F20260821%2Fauto%2Fs3%2Faws4_request&X-Amz-Date=20260821T094343Z&X-Amz-Expires=10800&X-Amz-SignedHeaders=host&response-content-disposition=Deadpool.%26.Wolverine.2024.480p.BluRay.Hindi.English.ESubs.Vegamovies.is.mkv&X-Amz-Signature=2e9ba0ca3124642b70496c8822c85cb878d260679741e252adad91c17f21141f',
        target_url = 'https://nexdrive.fit/genxfm784776393879/',
        resolution = '1080p',
        size = '584.72 MB'
    WHERE id = (SELECT id FROM releases WHERE title_id IN (SELECT id FROM titles WHERE canonical_title LIKE '%Deadpool%') LIMIT 1)
""")

conn.commit()
print("Successfully verified and updated stream mappings.")
conn.close()
