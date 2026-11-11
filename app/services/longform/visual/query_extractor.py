"""Smart short keyword extractor for stock footage search (Stage 7B.1 v3, X3).

Extracts 3-6 concise, high-relevance search keywords from narration sentences or [search: ...] tags:
- Prioritizes [search: ...] tag from script if present.
- Removes English and Indonesian stopwords.
- Prioritizes domain/subject nouns and terms.
- Complements with 1-2 preset visual_keywords.
- Produces concise 3-6 word queries optimal for stock APIs (Pixabay/Coverr/Pexels).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# Comprehensive stopwords for English and Indonesian
STOP_WORDS = {
    # English
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both",
    "but", "by", "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't",
    "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "had", "hadn't",
    "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll",
    "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's",
    "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on",
    "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own",
    "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some",
    "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then",
    "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we",
    "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's",
    "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's", "with",
    "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "just", "now", "also", "even", "still", "like", "well", "back",
    "one", "two", "three", "first", "second", "many", "much", "became", "become", "comes",
    "came", "went", "goes", "seen", "saw", "said", "made", "make", "take", "took", "tell",
    # Indonesian
    "ada", "adalah", "adanya", "adapun", "agak", "agar", "akan", "akankah", "akhir", "akhiri",
    "akhirnya", "aku", "akulah", "amat", "amatlah", "anda", "andalah", "antar", "antara",
    "antaranya", "apa", "apaan", "apabila", "apakah", "apalagi", "apatah", "arti", "artinya",
    "asal", "asalkan", "atas", "atau", "ataukah", "ataupun", "awal", "awalnya", "bagai",
    "bagaikan", "bagaimana", "bagaimanakah", "bagaimanapun", "bagi", "bagian", "bahkan", "bahwa",
    "bahwasanya", "baik", "bakal", "bakalan", "balik", "banyak", "bapak", "baru", "bawah",
    "beberapa", "begini", "beginian", "beginikah", "beginilah", "begitu", "begitukah", "begitulah",
    "begitupun", "bekerja", "belakang", "belakangan", "belum", "belumlah", "benar", "benarkah",
    "benarlah", "berada", "berakhir", "berakhirlah", "berakhirnya", "berapa", "berapakah",
    "berapalah", "berapapun", "berarti", "berawal", "berbagai", "berdatangan", "beri", "berikan",
    "berikut", "berikutnya", "berjumlah", "berkala", "berkali", "berkata", "berkehendak",
    "berkeinginan", "berkenaan", "berlainan", "berlalu", "berlangsung", "berlebihan", "bermacam",
    "bermaksud", "bermula", "bersama", "bersiap", "bertanya", "berturut", "bertutur", "berujar",
    "berupa", "besar", "betul", "betulkah", "biasa", "biasanya", "bila", "bilakah", "bisa",
    "bisakah", "boleh", "bolehkah", "bolehlah", "buat", "bukan", "bukankah", "bukanlah", "bukannya",
    "cuma", "percuma", "dahulu", "dalam", "dan", "dapat", "dari", "daripada", "dekat", "demi",
    "demikian", "demikianlah", "dengan", "depan", "di", "dia", "diakhiri", "diakhirinya",
    "dialah", "diantara", "diantaranya", "diberi", "diberikan", "diberikannya", "dibuat",
    "dibuatnya", "didapat", "didatangkan", "digunakan", "dihendaki", "dihasratkan", "dijawab",
    "dijelaskan", "dijelaskannya", "dikarenakan", "dikatakan", "dikatakannya", "dikerjakan",
    "diketahui", "diketahuinya", "dikira", "dilakukan", "dilalui", "dilihat", "dimaksud",
    "dimaksudkan", "dimaksudkannya", "dimaksudnya", "diminta", "dimintai", "dimisalkan", "dimulai",
    "dimulailah", "dimulainya", "dimungkinkan", "dini", "dipastikan", "diperbuat", "diperbuatnya",
    "dipergunakan", "diperkirakan", "diperlihatkan", "diperlukan", "diperlukannya", "dipersoalkan",
    "dipertanyakan", "dipunyai", "diri", "dirinya", "disampaikan", "disebut", "disebutkan",
    "disebutkannya", "disini", "disinilah", "ditambahkan", "ditandaskan", "ditanya", "ditanyai",
    "ditanyakan", "ditegaskan", "ditemukan", "ditentukan", "ditentukannya", "dituturkan",
    "dituturkannya", "diucapkan", "diucapkannya", "diungkapkan", "dong", "dulu", "empat",
    "enggak", "enggaknya", "entah", "entahlah", "guna", "gunakan", "hal", "hampir", "hanya",
    "hanyalah", "hari", "harus", "haruslah", "harusnya", "hendak", "hendaklah", "hendaknya",
    "hingga", "ia", "ialah", "ibarat", "ibaratkan", "ibaratnya", "ibu", "ikut", "ingat",
    "ingin", "inginkah", "inginkan", "ini", "inikah", "inilah", "itu", "itukah", "itulah",
    "jadi", "jadilah", "jadinya", "jangan", "jangankan", "janganlah", "jauh", "jawab",
    "jawaban", "jawabnya", "jelas", "jelaskan", "jelaslah", "jelasnya", "jika", "jikalau",
    "juga", "jumlah", "jumlahnya", "justru", "kala", "kalau", "kalaulah", "kalaupun", "kalian",
    "kami", "kamilah", "kamu", "kamulah", "kan", "kapan", "kapankah", "kapanpun", "karena",
    "karenanya", "kasus", "kata", "katakan", "katakanlah", "katanya", "ke", "keadaan",
    "kebetulan", "kecil", "kedua", "keduanya", "keinginan", "kelamaan", "kelihatan", "kelihatannya",
    "kelima", "keluar", "kembali", "kemudian", "kemungkinan", "kemungkinannya", "kenapa", "kepada",
    "kepadanya", "kesampaian", "keseluruhan", "keseluruhannya", "keterlaluan", "ketika", "khususnya",
    "kini", "kinilah", "kira", "kiranya", "kita", "kitalah", "kurang", "lagi", "lagian", "lah",
    "lain", "lainnya", "lalu", "lama", "lamanya", "lanjut", "lanjutnya", "lebih", "lewat",
    "macam", "maka", "makanya", "makin", "malah", "malahan", "mampu", "mampukah", "mana",
    "manakala", "manalagi", "masih", "masihkah", "masing", "mau", "maupun", "melainkan",
    "melakukan", "melalui", "melihat", "melihatnya", "memang", "memastikan", "memberi", "memberikan",
    "membuat", "memerlukan", "memihak", "meminta", "memintakan", "memisalkan", "memperbuat",
    "mempergunakan", "memperkirakan", "memperlihatkan", "mempersiapkan", "mempersoalkan",
    "mempertanyakan", "mempunyai", "memulai", "memungkinkan", "menaiki", "menandaskan", "menanti",
    "menantikan", "menanya", "menanyai", "menanyakan", "mendapat", "mendapatkan", "mendatang",
    "mendatangi", "mendatangkan", "menegaskan", "mengakhiri", "mengapa", "mengatakan", "mengatakannya",
    "mengenai", "mengerjakan", "mengetahui", "menghendaki", "mengira", "mengucapkan", "mengucapkannya",
    "mengungkapkan", "menjadi", "menjawab", "menuju", "menunjuk", "menunjuki", "menunjukkan",
    "menunjuknya", "menurut", "menuturkan", "menyampaikan", "menyangkut", "menyatakan", "menyebutkan",
    "menyeluruh", "menyiapkan", "merasa", "mereka", "merekalah", "merupakan", "meski", "meskipun",
    "meyakini", "meyakinkan", "minta", "mirip", "misal", "misalkan", "misalnya", "mula", "mulai",
    "mulailah", "mulanya", "mungkin", "mungkinkah", "nah", "naik", "namun", "nanti", "nantinya",
    "nyaris", "nyatanya", "olah", "oleh", "olehnya", "pada", "padahal", "padanya", "pak", "paling",
    "panjang", "pantas", "para", "pasti", "pastilah", "penting", "pentingnya", "per", "percuma",
    "perlu", "perlukah", "perlunya", "pernah", "persoalan", "pertama", "pertamatama", "pertanyaan",
    "pertanyakan", "pihak", "pihaknya", "pukul", "pula", "pun", "punya", "rasa", "rasanya",
    "rata", "rupanya", "saat", "saatnya", "saja", "sajalah", "saling", "sama", "samakah",
    "sambil", "sampai", "sampaikan", "sana", "sangat", "sangatlah", "satu", "saya", "sayalah",
    "se", "sebab", "sebabnya", "sebagai", "sebagaimana", "sebagainya", "sebagian", "sebaik",
    "sebaiknya", "sebaliknya", "sebanyak", "sebegini", "sebegitu", "sebelum", "sebelumnya",
    "sebenarnya", "seberapa", "sebesar", "sebetulnya", "sebisanya", "sebuah", "sebut", "sebutlah",
    "sebutnya", "secara", "secukupnya", "sedang", "sedangkan", "sedemikian", "sedikit", "sedikitnya",
    "setidaknya", "sesuatu", "seseorang", "setiap", "seterusnya", "siapa", "siapakah", "siapapun",
    "sudah", "sudahlah", "supaya", "tahu", "tahun", "tak", "tapi", "tanpa", "telah", "tentang",
    "tentu", "terhadap", "termasuk", "tersebut", "terus", "tetap", "tetapi", "tidak", "waduh",
    "wah", "wahai", "yaitu", "yakni", "yang",
}

# Generic words that do not help in visual searches
GENERIC_WORDS = {
    "greatest", "unsolved", "riddle", "riddles", "story", "stories", "legend", "legends",
    "history", "historical", "fate", "day", "days", "year", "years", "time", "times",
    "thing", "things", "part", "parts", "world", "true", "truth", "fact", "facts", "moment",
    "mystery", "mysterious", "secret", "secrets", "unknown", "known", "unclear", "forever",
    "ever", "never", "always", "someone", "anyone", "everyone", "nobody", "something",
    "cerita", "kisah", "sejarah", "misteri", "rahasia", "fakta", "zaman", "waktu",
    "dunia", "hari", "tahun", "saat", "momen", "sesuatu", "semua", "orang",
}


def extract_short_search_query(
    text: str,
    visual_keywords: Optional[List[str]] = None,
    topic_keywords: Optional[List[str]] = None,
    required_subject_terms: Optional[List[str]] = None,
    max_words: int = 5,
) -> str:
    """Extract a concise 3-5 word search query for a shot (X3).

    1. If text contains `[search: ...]` tag, extracts and returns the tag content.
    2. Otherwise, cleans narration sentence, discards stopwords, generic words, and numbers,
       prioritizes domain/proper nouns, and blends 1-2 visual keywords.
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    # 1. Check for explicit [search: ...] tag
    search_tag_match = re.search(r"\[search:\s*([^\]]+)\]", raw, re.IGNORECASE)
    if search_tag_match:
        tag_content = search_tag_match.group(1).strip()
        # Clean up any quotes or excess whitespace
        tag_content = re.sub(r'["\']', '', tag_content).strip()
        if tag_content:
            return tag_content

    # Remove any markdown or verification tags like [verify: ...] or [pause: ...]
    clean_text = re.sub(r"\[[^\]]+\]", "", raw)

    # 2. Extract potential words
    tokens = re.findall(r"[A-Za-z0-9]+(?:'[a-z]+)?", clean_text)
    meaningful: List[str] = []

    # Preserve subject terms from required_subject_terms first if mentioned
    subject_pref = ""
    req_list = [r.lower().strip() for r in (required_subject_terms or []) if r.strip()]
    lower_raw = clean_text.lower()
    for req in req_list:
        if re.search(r"\b" + re.escape(req) + r"\b", lower_raw):
            subject_pref = req
            break

    for tok in tokens:
        low = tok.lower()
        if len(low) <= 2:
            continue
        # Skip pure digits unless it's a 4-digit year like 1969 or decade like 90s
        if low.isdigit() and len(low) != 4:
            continue
        if low in STOP_WORDS or low in GENERIC_WORDS:
            continue
        if low not in [m.lower() for m in meaningful]:
            meaningful.append(tok.lower())

    chosen_words: List[str] = []
    if subject_pref:
        for sub_w in subject_pref.split():
            if sub_w not in chosen_words:
                chosen_words.append(sub_w)

    for w in meaningful:
        if w not in chosen_words:
            chosen_words.append(w)
        if len(chosen_words) >= 4:
            break

    # If too few words (< 3), pull from visual_keywords or topic_keywords
    extra_pool = (visual_keywords or []) + (topic_keywords or [])
    for extra in extra_pool:
        if len(chosen_words) >= max_words:
            break
        for kw in re.findall(r"[A-Za-z0-9]+", extra.lower()):
            if kw not in STOP_WORDS and kw not in GENERIC_WORDS and len(kw) > 2 and kw not in chosen_words:
                chosen_words.append(kw)
                if len(chosen_words) >= max_words:
                    break

    # Guarantee at least 2 words if possible, capped at max_words
    result = " ".join(chosen_words[:max_words]).strip()
    return result if result else clean_text[:40].strip()
