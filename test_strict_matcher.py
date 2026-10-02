import re
import difflib

def clean_media_title(raw_title: str) -> str:
    """Removes website noise from title."""
    noise = r'\b(Download|Full Movie|Watch Online|Direct Link|ESubs|Multi Audio|Dual Audio|ORG 5\.1|WEB-DL|WEBRip|BluRay|HDRip|DVDRip|\d{3,4}p|2160p|4K|1080p|720p|480p|Restored|Malayalam|Hindi|Tamil|Telugu|English|Kannada|the Movie|Hindi Dubbed|Line Audio|DTS-HD|HQ|Clean Audio|HQ-Line Audio|Remastered|Unrated|Extended)\b'
    cleaned = re.sub(noise, '', raw_title, flags=re.I)
    cleaned = re.sub(r'[\{\}\[\]\(\)\|–—:]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

ROMAN_NUMS = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"
}

def normalize_tokens(s: str) -> list[str]:
    """Tokenize and convert roman / word numbers to digits for robust comparison."""
    words = re.findall(r'[a-zA-Z0-9]+', s.lower())
    norm = []
    for w in words:
        if w in ROMAN_NUMS:
            norm.append(ROMAN_NUMS[w])
        else:
            norm.append(w)
    return norm

def is_strict_match(query: str, candidate_title: str) -> bool:
    """Returns True only if candidate title is genuinely the intended movie or series."""
    q = query.strip().lower().replace('é', 'e')
    c = clean_media_title(candidate_title).lower().replace('é', 'e')

    # Tokenized normalized words
    q_tokens = normalize_tokens(q)
    c_tokens = normalize_tokens(c)

    # Check digit / number integrity: e.g. "Jujutsu Kaisen 0" vs "Jujutsu Kaisen Season 1"
    # or "John Wick 4" vs "John Wick 2" or "Dune Part Two" vs "Dune (2021)"
    q_nums = set(w for w in q_tokens if w.isdigit() and len(w) <= 2) # e.g. 0, 1, 2, 3, 4
    c_nums = set(w for w in c_tokens if w.isdigit() and len(w) <= 2)

    # If query specifically asks for a part/chapter/number (like 2, 4, 0) and candidate does not have it
    if q_nums:
        for num in q_nums:
            if num not in c_nums:
                return False

    # Extract year if present in both
    q_year = re.search(r'\b(19\d\d|20\d\d)\b', q)
    c_year = re.search(r'\b(19\d\d|20\d\d)\b', candidate_title)
    if q_year and c_year:
        if abs(int(q_year.group(1)) - int(c_year.group(1))) > 1:
            return False

    q_no_year = re.sub(r'\b(19\d\d|20\d\d)\b', '', q).strip()
    c_no_year = re.sub(r'\b(19\d\d|20\d\d)\b', '', c).strip()

    q_no_y_tokens = normalize_tokens(q_no_year or q)
    c_no_y_tokens = normalize_tokens(c_no_year or c)

    # Direct match
    q_str = " ".join(q_no_y_tokens)
    c_str = " ".join(c_no_y_tokens)

    if q_str == c_str:
        return True

    # Word intersection check for multi-word titles
    stop_words = {'movie', 'series', 'download', 'watch', 'online', 'the', 'a', 'an', 'and', 'of', 'in', 'part', 'chapter'}
    q_keywords = [w for w in q_no_y_tokens if len(w) > 1 and w not in stop_words]
    c_keywords = [w for w in c_no_y_tokens if len(w) > 1 and w not in stop_words]

    if q_keywords:
        # Check how many query keywords are present in candidate
        matched = [w for w in q_keywords if w in c_keywords or any(w in cw or cw in w for cw in c_keywords if len(cw) > 3 and len(w) > 3)]
        if len(matched) == len(q_keywords) or (len(q_keywords) >= 3 and len(matched) / len(q_keywords) >= 0.75):
            return True

    # Substring check with whole word boundaries
    if f" {q_str} " in f" {c_str} ":
        return True

    ratio = difflib.SequenceMatcher(None, q_str, c_str).ratio()
    return ratio >= 0.75

def run_tests():
    test_cases = [
        # (Query, Candidate, Expected)
        ("Avengers: Endgame", "Download Avengers: Endgame (2019) Dual Audio 480p", True),
        ("Kummatty (1979)", "Kummatty (1979) Malayalam Movie WEB-DL", True),
        ("Kummatty (1979)", "Download 1941 (1979) Directors Cut", False),
        ("Dune: Part Two", "Download Dune: Part Two (2024) Dual Audio", True),
        ("Dune: Part Two", "Dune – Part Two (2024) 720p BluRay", True),
        ("Dune: Part Two", "Dune (2021) 1080p BluRay", False),
        ("Dune Part 2", "Dune: Part Two (2024)", True),
        ("John Wick: Chapter 4", "Download John Wick: Chapter 4 (2023) BluRay", True),
        ("John Wick: Chapter 4", "Download John Wick: Chapter 2 (2017) BluRay", False),
        ("Spider-Man Across the Spider-Verse", "Download Kiss of the Spider Woman (2025)", False),
        ("Spider-Man Across the Spider-Verse", "Spider-Man: Across the Spider-Verse (2023) 720p", True),
        ("Jujutsu Kaisen 0", "Jujutsu Kaisen Season 1 (2020)", False),
        ("Jujutsu Kaisen 0", "Jujutsu Kaisen 0 The Movie (2021)", True),
        ("Manjummel Boys", "Download The Boys (Season 5)", False),
        ("Manjummel Boys", "Manjummel Boys (2024) 1080p DSNP WEB-DL", True),
        ("Kantara", "Download Den of Thieves 2: Pantera (2025)", False),
        ("Kantara", "Kantara (2022) 1080p AMZN WEB-DL", True),
    ]

    print("Running is_strict_match test cases:")
    passed = 0
    for q, c, exp in test_cases:
        actual = is_strict_match(q, c)
        res = "PASS" if actual == exp else "FAIL"
        if actual == exp:
            passed += 1
        print(f"[{res}] Q: '{q}' | Candidate: '{c[:50]}' -> Expected: {exp}, Got: {actual}")
    print(f"\nResult: {passed}/{len(test_cases)} passed.")

if __name__ == "__main__":
    run_tests()
