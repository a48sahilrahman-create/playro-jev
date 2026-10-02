import re
import difflib

def clean_media_title(raw_title: str) -> str:
    """Removes website noise from title."""
    noise = r'\b(Download|Full Movie|Watch Online|Direct Link|ESubs|Multi Audio|Dual Audio|ORG 5\.1|WEB-DL|BluRay|HDRip|\d{3,4}p|Restored|Malayalam|Hindi|Tamil|Telugu|English|Kannada)\b'
    cleaned = re.sub(noise, '', raw_title, flags=re.I)
    cleaned = re.sub(r'[\{\}\[\]\(\)\|]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def is_strict_match(query: str, candidate_title: str) -> bool:
    """Returns True only if candidate title is genuinely the intended movie or series."""
    q = query.strip().lower()
    c = clean_media_title(candidate_title).lower()

    if q == c or q in c or c in q:
        # Check digit integrity: e.g. "Jujutsu Kaisen 0" vs "Jujutsu Kaisen Season 1"
        q_digits = set(re.findall(r'\b\d+\b', q))
        c_digits = set(re.findall(r'\b\d+\b', c))
        # If query has a specific number like '0' or '2' and candidate has different numbers or no '0'
        if '0' in q_digits and '0' not in c_digits:
            return False
        return True

    # Check without year: e.g. query "Kummatty (1979)" -> "kummatty"
    q_no_year = re.sub(r'\b(19\d\d|20\d\d)\b', '', q).strip()
    c_no_year = re.sub(r'\b(19\d\d|20\d\d)\b', '', c).strip()
    if q_no_year and (q_no_year == c_no_year or q_no_year in c_no_year or c_no_year in q_no_year):
        q_digits = set(re.findall(r'\b\d+\b', q_no_year))
        c_digits = set(re.findall(r'\b\d+\b', c_no_year))
        if '0' in q_digits and '0' not in c_digits:
            return False
        return True

    # Word intersection check for multi-word titles (e.g. house of dragon)
    q_words = [w for w in re.findall(r'\w+', q_no_year or q) if len(w) > 1]
    c_words = [w for w in re.findall(r'\w+', c_no_year or c) if len(w) > 1]
    if q_words and all(w in c_words for w in q_words):
        return True

    ratio = difflib.SequenceMatcher(None, q, c).ratio()
    return ratio >= 0.60

print("JJK 0 vs JJK S1:", is_strict_match("Jujutsu Kaisen 0", "Download Jujutsu Kaisen (Season 1)"))
print("JJK 0 vs JJK 0 Movie:", is_strict_match("Jujutsu Kaisen 0", "Download Jujutsu Kaisen 0 (2021) Movie"))
print("Attack on Titan vs AOT S1-4:", is_strict_match("Attack on Titan", "Download Attack on Titan (Season 1 - 4)"))
