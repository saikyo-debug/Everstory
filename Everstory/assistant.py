"""
Everstory - personal diary assistant.

The assistant runs entirely inside the app. Diary text is never sent
to an outside service, which is the safest default for private
memories. It can:

* search your memories (with related words, so "party" also finds
  "birthday" and "cake", in English and Filipino),
* understand date phrases such as "last month", "in March 2025",
  "yesterday" or "past 7 days",
* show "on this day" memories from earlier years,
* find words related to a word, using a built-in word map and the
  words that appear alongside it in your own entries,
* give diary statistics and your writing streak,
* suggest a writing prompt,
* answer "how do I ...?" questions about Everstory.

The engine (`respond`) is a plain function that works on lists of
dictionaries, so it is easy to test and has no database dependency.
"""

import math
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request, url_for

import database
from auth_utils import current_user, login_required


assistant_bp = Blueprint("assistant", __name__)

MAX_MESSAGE_LENGTH = 300
MAX_RESULTS = 6


# =========================================
# WORDS
# =========================================

STOPWORDS = frozenset("""
a about above after again all also am an and any are as at be because been
before being below between both but by can could did do does doing down
during each few for from further had has have having he her here hers him
his how i if in into is it its just me more most my no nor not now of off
on once only or other our out over own same she should so some such than
that the their them then there these they this those through to too under
until up us very was we were what when where which while who whom why will
with would you your yours
ang ng sa na at ay mga ko mo ako ikaw siya kami kayo sila ito iyon yun yung
para pero kasi po din rin lang naman nang may wala si ni kay hindi oo ba pa
nga ka ba daw raw din
""".split())

# Groups of words that belong together. A search for any word in a
# group is widened to the others. Filipino words are included because
# diaries are often written in Filipino or in a mix of both.
WORD_GROUPS = [
    ["family", "mother", "mom", "mama", "father", "dad", "papa", "sister",
     "brother", "parents", "cousin", "grandmother", "grandfather", "lola",
     "lolo", "pamilya", "nanay", "tatay", "ate", "kuya", "kapatid"],
    ["friend", "friends", "bestfriend", "barkada", "classmate", "buddy",
     "kaibigan", "tropa"],
    ["love", "crush", "partner", "boyfriend", "girlfriend", "romance",
     "date", "mahal", "pagmamahal", "pag-ibig"],
    ["birthday", "party", "celebration", "cake", "gift", "present",
     "anniversary", "kaarawan", "handaan", "regalo"],
    ["christmas", "newyear", "holiday", "noche", "pasko", "bagong"],
    ["happy", "joy", "glad", "excited", "grateful", "smile", "laugh",
     "proud", "masaya", "tuwa", "saya", "galak"],
    ["sad", "cry", "crying", "lonely", "miss", "hurt", "grief", "tears",
     "malungkot", "lungkot", "iyak", "nasaktan"],
    ["angry", "mad", "upset", "annoyed", "frustrated", "irritated",
     "galit", "inis", "badtrip"],
    ["tired", "exhausted", "sleepy", "rest", "sleep", "nap", "pagod",
     "antok", "tulog"],
    ["anxious", "worried", "nervous", "stress", "stressed", "afraid",
     "scared", "kinakabahan", "kaba", "takot", "pangamba"],
    ["school", "class", "exam", "test", "teacher", "student", "homework",
     "study", "thesis", "project", "grade", "paaralan", "eskwela",
     "klase", "pagsusulit", "guro", "aral"],
    ["work", "job", "office", "boss", "colleague", "meeting", "career",
     "salary", "trabaho", "opisina"],
    ["food", "eat", "dinner", "lunch", "breakfast", "meal", "cook",
     "cooking", "restaurant", "snack", "coffee", "pagkain", "kain",
     "ulam", "kape", "merienda"],
    ["travel", "trip", "vacation", "beach", "flight", "journey", "tour",
     "road", "biyahe", "byahe", "bakasyon", "dagat", "pasyal"],
    ["weather", "rain", "raining", "rainy", "storm", "stormy", "typhoon",
     "hot", "cold", "sunny", "cloudy", "wind", "windy", "ulan", "bagyo",
     "init", "lamig", "hangin"],
    ["church", "pray", "prayer", "god", "faith", "mass", "bible",
     "simbahan", "dasal", "panalangin", "misa", "diyos"],
    ["health", "sick", "doctor", "hospital", "medicine", "fever",
     "headache", "gym", "exercise", "workout", "run", "walk", "may-sakit",
     "sakit", "ospital", "lagnat"],
    ["money", "pay", "paid", "salary", "budget", "savings", "spend",
     "bills", "loan", "pera", "sweldo", "bayad", "ipon", "utang"],
    ["home", "house", "room", "kitchen", "garden", "neighbor", "bahay",
     "kwarto", "kapitbahay"],
    ["pet", "dog", "cat", "puppy", "kitten", "aso", "pusa"],
    ["music", "song", "sing", "concert", "guitar", "band", "dance",
     "kanta", "awit", "sayaw"],
    ["goal", "dream", "plan", "hope", "future", "wish", "ambition",
     "pangarap", "plano", "pag-asa"],
    ["night", "evening", "midnight", "moon", "stars", "gabi", "buwan",
     "bituin"],
    ["morning", "sunrise", "dawn", "umaga", "madaling-araw"],
]

SUFFIXES = ("ing", "edly", "ed", "ies", "es", "ly", "s")


def tokenize(text):
    """Lowercase words, keeping letters (including accented ones)."""

    words = re.findall(
        r"[^\W\d_]+(?:[-'][^\W\d_]+)*", (text or "").lower()
    )

    return [
        word[:-2] if word.endswith("'s") and len(word) > 3 else word
        for word in words
    ]


def stem(word):
    """A very light stemmer: 'parties' -> 'part', 'walked' -> 'walk'."""

    if len(word) <= 3:
        return word

    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "ies":
                return word[:-3] + "y"

            return word[:-len(suffix)]

    return word


def content_tokens(text):
    return [
        word for word in tokenize(text)
        if word not in STOPWORDS and len(word) > 1
    ]


def _build_group_index():
    index = {}

    for group_number, group in enumerate(WORD_GROUPS):
        for word in group:
            index.setdefault(stem(word), set()).add(group_number)
            index.setdefault(word, set()).add(group_number)

    return index


GROUP_INDEX = _build_group_index()


def related_from_word_map(word):
    """Words that sit in the same group(s) as `word`."""

    group_numbers = (
        GROUP_INDEX.get(word, set()) | GROUP_INDEX.get(stem(word), set())
    )

    related = []

    for group_number in sorted(group_numbers):
        for candidate in WORD_GROUPS[group_number]:
            if candidate != word and candidate not in related:
                related.append(candidate)

    return related


# =========================================
# DATES
# =========================================

MONTH_NAMES = {
    name.lower(): number
    for number, name in enumerate([
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december"
    ], start=1)
}

MONTH_ABBREVIATIONS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _month_range(year, month):
    start = date(year, month, 1)

    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)

    return start, end


def parse_date_phrase(message, today):
    """
    Find a date phrase in the message.

    Returns (start_date, end_date, label, message_without_phrase) or
    None when no date phrase is present.
    """

    text = message.lower()

    def cut(pattern):
        return re.sub(pattern, " ", text, count=1).strip()

    match = re.search(r"\b(?:past|last)\s+(\d{1,3})\s+days?\b", text)

    if match:
        days = max(1, min(int(match.group(1)), 3650))
        return (
            today - timedelta(days=days - 1),
            today,
            f"the last {days} days",
            cut(match.group(0))
        )

    if re.search(r"\byesterday\b", text):
        day = today - timedelta(days=1)
        return day, day, "yesterday", cut(r"\byesterday\b")

    if re.search(r"\btoday\b", text):
        return today, today, "today", cut(r"\btoday\b")

    if re.search(r"\bthis week\b", text):
        start = today - timedelta(days=today.weekday())
        return start, today, "this week", cut(r"\bthis week\b")

    if re.search(r"\blast week\b", text):
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(days=7)
        end = this_monday - timedelta(days=1)
        return start, end, "last week", cut(r"\blast week\b")

    if re.search(r"\bthis month\b", text):
        start, end = _month_range(today.year, today.month)
        return start, min(end, today), "this month", cut(r"\bthis month\b")

    if re.search(r"\blast month\b", text):
        year, month = today.year, today.month - 1

        if month == 0:
            year, month = year - 1, 12

        start, end = _month_range(year, month)
        return start, end, "last month", cut(r"\blast month\b")

    if re.search(r"\bthis year\b", text):
        return (
            date(today.year, 1, 1),
            today,
            "this year",
            cut(r"\bthis year\b")
        )

    if re.search(r"\blast year\b", text):
        year = today.year - 1
        return (
            date(year, 1, 1),
            date(year, 12, 31),
            "last year",
            cut(r"\blast year\b")
        )

    month_words = "|".join(
        sorted(
            list(MONTH_NAMES) + list(MONTH_ABBREVIATIONS),
            key=len,
            reverse=True
        )
    )

    match = re.search(
        rf"\b(?:in|during|on|of)?\s*({month_words})\b\.?(?:\s+(\d{{4}}))?",
        text
    )

    if match:
        word = match.group(1)
        month = MONTH_NAMES.get(word) or MONTH_ABBREVIATIONS.get(word)

        # "may" is also a common verb, so require a year or a
        # preposition before it to avoid false matches.
        looks_like_date = (
            word != "may"
            or match.group(2) is not None
            or re.search(r"\b(?:in|during|of)\s+may\b", text)
        )

        if month and looks_like_date:
            year = int(match.group(2)) if match.group(2) else None

            if year is None:
                year = today.year if month <= today.month else today.year - 1

            if 1900 <= year <= 9999:
                start, end = _month_range(year, month)
                label = f"{start.strftime('%B')} {year}"
                return start, end, label, cut(match.group(0))

    match = re.search(r"\b(?:in|during|of)\s+(\d{4})\b", text)

    if match:
        year = int(match.group(1))

        if 1900 <= year <= 9999:
            return (
                date(year, 1, 1),
                date(year, 12, 31),
                str(year),
                cut(match.group(0))
            )

    return None


# =========================================
# ENTRIES
# =========================================

def _entry_date(entry):
    try:
        return datetime.fromisoformat(str(entry["created_at"])).date()
    except (ValueError, TypeError):
        return None


def _snippet(content, terms, length=150):
    flat = re.sub(r"\s+", " ", content or "").strip()

    if not flat:
        return ""

    lowered = flat.lower()
    position = -1

    for term in terms:
        found = lowered.find(term)

        if found != -1 and (position == -1 or found < position):
            position = found

    if position == -1:
        position = 0

    start = max(0, position - 50)
    end = min(len(flat), start + length)
    snippet = flat[start:end].strip()

    if start > 0:
        snippet = "…" + snippet

    if end < len(flat):
        snippet = snippet + "…"

    return snippet


def _result(entry, urls, terms=()):
    entry_day = _entry_date(entry)

    return {
        "id": entry["id"],
        "title": entry["title"],
        "date": entry_day.strftime("%B %d, %Y") if entry_day else "",
        "snippet": _snippet(entry["content"], terms),
        "url": urls["entry"].replace("__ID__", str(entry["id"])),
        "favorite": bool(entry["is_favorite"]),
    }


def _in_range(entry, start, end):
    entry_day = _entry_date(entry)

    return entry_day is not None and start <= entry_day <= end


def rank_entries(entries, query_terms, expansion_terms=()):
    """
    Score entries against the query.

    Title matches count three times as much as text matches. Words
    found through the word map count half as much as words the person
    actually typed. Rarer words count for more (inverse document
    frequency).
    """

    documents = []

    for entry in entries:
        title_tokens = [stem(w) for w in content_tokens(entry["title"])]
        body_tokens = [stem(w) for w in content_tokens(entry["content"])]
        documents.append((entry, Counter(title_tokens), Counter(body_tokens)))

    total = max(1, len(documents))
    document_frequency = Counter()

    for _entry, title_counts, body_counts in documents:
        for word in set(title_counts) | set(body_counts):
            document_frequency[word] += 1

    def weight(word):
        return math.log(1 + total / (1 + document_frequency.get(word, 0)))

    def term_score(term, title_counts, body_counts, factor):
        score = 0.0

        if term in title_counts or term in body_counts:
            score += factor * weight(term) * (
                3 * min(title_counts.get(term, 0), 3)
                + (1 + math.log(1 + body_counts.get(term, 0)))
                * (1 if term in body_counts else 0)
            )
            return score

        # Prefix match: "birth" finds "birthday".
        if len(term) >= 4:
            for word in list(title_counts) + list(body_counts):
                if word.startswith(term):
                    score += factor * 0.6 * weight(word)
                    break

        return score

    stemmed_query = [stem(term) for term in query_terms]
    stemmed_expansion = [
        stem(term) for term in expansion_terms
        if stem(term) not in stemmed_query
    ]

    ranked = []

    for entry, title_counts, body_counts in documents:
        score = 0.0
        matched_all = True

        for term in stemmed_query:
            term_value = term_score(term, title_counts, body_counts, 1.0)

            if term_value == 0:
                matched_all = False

            score += term_value

        for term in stemmed_expansion:
            score += term_score(term, title_counts, body_counts, 0.5)

        if score > 0:
            # Entries containing every typed word rank above partial hits.
            ranked.append((score + (5 if matched_all else 0), entry))

    ranked.sort(
        key=lambda pair: (pair[0], str(pair[1]["created_at"])),
        reverse=True
    )

    return [entry for _score, entry in ranked]


def related_words(term, entries, limit=8):
    """
    Words related to `term`: the built-in word map plus the words that
    appear in the same entries as `term` in this person's diary.
    """

    term = term.lower().strip()
    term_stem = stem(term)

    from_map = related_from_word_map(term)[:limit]

    containing = []

    for entry in entries:
        tokens = {
            stem(w) for w in content_tokens(
                f"{entry['title']} {entry['content']}"
            )
        }

        if term_stem in tokens:
            containing.append(entry)

    total = max(1, len(entries))
    document_frequency = Counter()

    for entry in entries:
        for word in {
            stem(w) for w in content_tokens(
                f"{entry['title']} {entry['content']}"
            )
        }:
            document_frequency[word] += 1

    together = Counter()

    for entry in containing:
        words = {
            w for w in content_tokens(
                f"{entry['title']} {entry['content']}"
            )
            if len(w) >= 3 and stem(w) != term_stem
        }

        for word in words:
            together[word] += 1

    scored = []

    for word, count in together.items():
        if count < 2 and len(containing) > 2:
            continue

        score = count * math.log(
            1 + total / (1 + document_frequency[stem(word)])
        )
        scored.append((score, word))

    scored.sort(reverse=True)

    from_diary = [
        word for _score, word in scored[:limit] if word not in from_map
    ]

    return from_map, from_diary, len(containing)


# =========================================
# HELP
# =========================================

PROMPTS = [
    "What is one small thing that made you smile today?",
    "Who made a difference in your day, and what did they do?",
    "Describe a place you went to this week as if showing it to a friend.",
    "What are you grateful for right now that you usually overlook?",
    "Write about a meal you will always remember.",
    "What is something you learned recently, big or small?",
    "Write a note to yourself one year from now.",
    "What was the hardest part of your week, and what helped you through it?",
    "Describe the sounds you can hear right now.",
    "Who do you miss, and what would you tell them?",
    "What is a song that takes you back to a moment? Write about that moment.",
    "What did you do today that you are proud of?",
    "Describe your perfect ordinary day.",
    "What is something you are looking forward to?",
    "Write about a person who taught you something important.",
    "What would you tell your younger self?",
    "What does home mean to you today?",
    "Write about a small act of kindness you gave or received.",
    "What are three things that went well today?",
    "What are you holding onto that you are ready to let go of?",
]

HELP_TOPICS = [
    (
        ("write", "new entry", "add a memory", "add entry", "create entry",
         "new memory", "start"),
        "To write a memory, choose “Write a Memory”. Give it a title, write "
        "what is on your mind and save it. You can add photos, a voice note "
        "or a video at the same time.",
        "new_entry"
    ),
    (
        ("edit", "change", "update", "modify", "fix", "rewrite"),
        "Open any memory and choose Edit to change its title or text, or to "
        "add and remove attachments.",
        "entries"
    ),
    (
        ("delete", "remove", "erase", "burahin"),
        "Open the memory and choose Delete. You will be asked to confirm, "
        "because deleted memories cannot be recovered.",
        "entries"
    ),
    (
        ("favorite", "favourite", "star", "heart"),
        "Tap the heart on a memory to mark it as a favorite. Use the "
        "Favorites filter on My Entries to see only those.",
        "entries"
    ),
    (
        ("voice", "record", "audio", "microphone"),
        "When you write or edit a memory, use the Record voice note button. "
        "Allow microphone access when your browser asks, press stop when you "
        "are done, then save the memory.",
        "new_entry"
    ),
    (
        ("photo", "picture", "image", "video", "attach", "upload"),
        "On the write or edit page, choose files under Photos, voice and "
        "video. You can attach up to 10 files to each memory.",
        "new_entry"
    ),
    (
        ("calendar", "date", "day"),
        "The Calendar shows a dot on every day you wrote a memory. Select a "
        "day to read what you saved.",
        "calendar"
    ),
    (
        ("capsule", "future", "seal", "unlock", "time capsule"),
        "A Time Capsule is a message to your future self. Choose when it "
        "should open; until then its contents stay sealed, even from you.",
        "capsules"
    ),
    (
        ("offline", "internet", "sync", "connection"),
        "Everstory keeps working without internet. Memories you write "
        "offline are saved on this device and sent to your diary "
        "automatically when you are back online.",
        "settings"
    ),
    (
        ("theme", "dark", "light", "font", "text size", "color", "colour",
         "appearance", "season"),
        "Open Settings to choose light or dark mode, a seasonal color theme, "
        "the font style and the text size.",
        "settings"
    ),
    (
        ("password", "security", "safe", "protect"),
        "You can change your password under Settings → Security. Changing it "
        "signs you out everywhere else.",
        "settings"
    ),
    (
        ("voice unlock", "voiceprint", "biometric", "passphrase"),
        "Voice unlock is optional. Under Settings → Voice unlock you can "
        "record a short passphrase. It then protects sensitive actions such "
        "as opening your Time Capsules.",
        "settings"
    ),
]

CAPABILITIES = (
    "I can search your memories (try “find birthday”, or “what did I write "
    "last month?”), show memories from this day in earlier years, suggest "
    "words related to a word, give you diary stats and your writing streak, "
    "suggest something to write about, and help you use Everstory."
)

SUGGESTIONS = [
    "On this day",
    "Writing prompt",
    "My stats",
    "Favorites",
    "What can you do?",
]


# =========================================
# THE ENGINE
# =========================================

def _reply(text, results=None, related=None, link=None):
    return {
        "reply": text,
        "results": results or [],
        "related": related or [],
        "link": link,
        "suggestions": SUGGESTIONS,
    }


def _writing_streak(entries, today):
    days = {d for d in (_entry_date(e) for e in entries) if d}

    if today in days:
        cursor = today
    elif (today - timedelta(days=1)) in days:
        cursor = today - timedelta(days=1)
    else:
        return 0

    streak = 0

    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)

    return streak


def _stats_reply(entries, today, urls):
    if not entries:
        return _reply(
            "Your diary is empty so far, so there is nothing to count yet. "
            "Your first memory will start the story.",
            link={"label": "Write a memory", "url": urls["new_entry"]}
        )

    days = {d for d in (_entry_date(e) for e in entries) if d}
    favorites = sum(1 for e in entries if e["is_favorite"])
    this_month = sum(
        1 for e in entries
        if (_entry_date(e) or date.min).replace(day=1)
        == today.replace(day=1)
    )
    streak = _writing_streak(entries, today)

    counts = Counter()

    for entry in entries:
        counts.update(content_tokens(f"{entry['title']} {entry['content']}"))

    top_words = [
        word for word, _count in counts.most_common(40)
        if len(word) >= 4
    ][:6]

    parts = [
        f"You have {len(entries)} "
        f"{'memory' if len(entries) == 1 else 'memories'} across "
        f"{len(days)} {'day' if len(days) == 1 else 'days'}.",
        f"{this_month} written this month.",
    ]

    if favorites:
        parts.append(
            f"{favorites} {'favorite' if favorites == 1 else 'favorites'}."
        )

    if streak >= 2:
        parts.append(f"You are on a {streak}-day writing streak. 🔥")
    elif streak == 1:
        parts.append("You wrote recently, so your streak is alive.")
    else:
        parts.append("A new memory today would start a fresh streak.")

    if top_words:
        parts.append("Words you use a lot: " + ", ".join(top_words) + ".")

    return _reply(" ".join(parts))


def respond(message, entries, today, urls, seed=None):
    """
    Answer one message.

    message  What the person typed.
    entries  Their entries (dict-like: id, title, content, created_at,
             is_favorite), newest first.
    today    A `date` (injected so the engine can be tested).
    urls     {"entry": "/entries/__ID__", "new_entry": ..., ...}
    """

    original = (message or "").strip()[:MAX_MESSAGE_LENGTH]
    lowered = original.lower()

    if not original:
        return _reply(
            "Ask me about your memories, or tap one of the suggestions below."
        )

    rng = random.Random(
        seed if seed is not None else today.toordinal() + len(original)
    )

    if re.fullmatch(
        r"(hi|hello|hey|kumusta|musta|good (morning|afternoon|evening))[ !.?]*",
        lowered
    ):
        return _reply("Hello! 💜 " + CAPABILITIES)

    if re.fullmatch(
        r"(help( me)?|what can you (do|help with)|how do you work|"
        r"what are you|tulong)[ ?.!]*",
        lowered
    ):
        return _reply(CAPABILITIES)

    # ----- how do I ... ----------------------------------------------
    if re.search(r"\b(how (do|can|to)|where (do|can|is)|paano)\b", lowered):
        best = None
        best_score = 0

        for keywords, answer, link_key in HELP_TOPICS:
            score = sum(
                (2 if " " in keyword else 1)
                for keyword in keywords
                if re.search(rf"\b{re.escape(keyword)}\b", lowered)
            )

            if score > best_score:
                best, best_score = (answer, link_key), score

        if best:
            answer, link_key = best
            return _reply(
                answer,
                link={
                    "label": "Open it",
                    "url": urls.get(link_key, urls["dashboard"])
                }
            )

    # ----- related words ----------------------------------------------
    match = re.search(
        r"(?:related|similar|connected|associated)\s+(?:words?|terms?)"
        r"(?:\s+(?:to|for|with|of))?\s+[\"“']?([^\s\"”'?.!,]+)"
        r"|(?:synonyms?|words?)\s+(?:for|like|related to)\s+"
        r"[\"“']?([^\s\"”'?.!,]+)",
        lowered
    )

    if match:
        term = match.group(1) or match.group(2)
        from_map, from_diary, in_entries = related_words(term, entries)

        if not from_map and not from_diary:
            return _reply(
                f"I could not find words related to “{term}” yet. Try a "
                "word you write about often."
            )

        pieces = []

        if from_map:
            pieces.append(
                f"Words connected to “{term}”: " + ", ".join(from_map) + "."
            )

        if from_diary:
            pieces.append(
                f"In your own diary, “{term}” shows up alongside: "
                + ", ".join(from_diary) + "."
            )

        return _reply(
            " ".join(pieces),
            related=(from_map + from_diary)[:12]
        )

    # ----- on this day -------------------------------------------------
    if re.search(
        r"\bon this day\b|\bthis day (last|in)\b|\bmemories from today\b",
        lowered
    ):
        hits = [
            e for e in entries
            if (d := _entry_date(e))
            and d.month == today.month
            and d.day == today.day
            and d.year < today.year
        ]

        if not hits:
            return _reply(
                "I could not find memories from this day in earlier years "
                "yet. Keep writing, and next year they will appear here."
            )

        return _reply(
            f"You have {len(hits)} "
            f"{'memory' if len(hits) == 1 else 'memories'} from "
            f"{today.strftime('%B %d')} in earlier years.",
            results=[_result(e, urls) for e in hits[:MAX_RESULTS]]
        )

    # ----- writing prompt ---------------------------------------------
    if re.search(
        r"\b(prompt|what should i write|inspire|inspiration|idea|"
        r"ano ang isusulat)\b",
        lowered
    ):
        choice = rng.sample(PROMPTS, 3)

        return _reply(
            "Here are a few ideas to start with:\n• "
            + "\n• ".join(choice),
            link={"label": "Write a memory", "url": urls["new_entry"]}
        )

    # ----- stats ---------------------------------------------------------
    if re.search(
        r"\b(stats?|statistics|how many|streak|summary|overview|count)\b",
        lowered
    ):
        return _stats_reply(entries, today, urls)

    # ----- favorites ----------------------------------------------------
    if re.search(r"\bfavou?rites?\b", lowered) and not re.search(
        r"\b(how|where)\b", lowered
    ):
        favorites = [e for e in entries if e["is_favorite"]]

        if not favorites:
            return _reply(
                "You have no favorite memories yet. Tap the heart on a "
                "memory to add it here."
            )

        return _reply(
            f"You have {len(favorites)} favorite "
            f"{'memory' if len(favorites) == 1 else 'memories'}.",
            results=[_result(e, urls) for e in favorites[:MAX_RESULTS]]
        )

    # ----- recent --------------------------------------------------------
    if re.search(
        r"\b(latest|most recent|recent|last (entry|memory)|newest)\b",
        lowered
    ):
        if not entries:
            return _reply("You have not written any memories yet.")

        return _reply(
            "Here are your most recent memories.",
            results=[_result(e, urls) for e in entries[:3]]
        )

    # ----- search ---------------------------------------------------------
    working = lowered
    date_filter = parse_date_phrase(original, today)
    scope = entries
    label = None

    if date_filter:
        start, end, label, remaining = date_filter
        working = remaining.lower()
        scope = [e for e in entries if _in_range(e, start, end)]

    working = re.sub(
        r"\b(find|search|show|me|my|memor(?:y|ies)|entries|entry|diary|"
        r"what|did|i|write|wrote|written|about|from|for|remember|anything|"
        r"something|tell|list|look|up|where|when|were|was|the|a|an|"
        r"help|please|give|get|want|need|like|can|could|would|you|any|"
        r"ano|nasulat|ko|hanapin)\b",
        " ",
        working
    )

    terms = [
        word for word in content_tokens(working) if len(word) >= 2
    ][:6]

    if not terms:
        if date_filter:
            if not scope:
                return _reply(f"I could not find any memories from {label}.")

            return _reply(
                f"You wrote {len(scope)} "
                f"{'memory' if len(scope) == 1 else 'memories'} "
                f"from {label}.",
                results=[_result(e, urls) for e in scope[:MAX_RESULTS]]
            )

        return _reply(
            "I did not catch what to look for. " + CAPABILITIES
        )

    expansion = []

    for term in terms:
        for candidate in related_from_word_map(term)[:10]:
            if candidate not in expansion and candidate not in terms:
                expansion.append(candidate)

    ranked = rank_entries(scope, terms, expansion)

    shown_terms = terms + [stem(t) for t in terms]

    if not ranked:
        where = f" from {label}" if label else ""
        related_hint = expansion[:6]

        text = f"I could not find memories about “{' '.join(terms)}”{where}."

        if related_hint:
            text += " Words you could try instead: " + ", ".join(
                related_hint
            ) + "."

        return _reply(text, related=related_hint)

    where = f" from {label}" if label else ""
    total = len(ranked)

    return _reply(
        f"I found {total} {'memory' if total == 1 else 'memories'} about "
        f"“{' '.join(terms)}”{where}."
        + (f" Showing the top {MAX_RESULTS}." if total > MAX_RESULTS else ""),
        results=[
            _result(entry, urls, shown_terms)
            for entry in ranked[:MAX_RESULTS]
        ],
        related=expansion[:8]
    )


# =========================================
# ROUTES
# =========================================

def _urls():
    return {
        "entry": url_for("view_entry", entry_id=0).replace("/0", "/__ID__"),
        "new_entry": url_for("new_entry"),
        "entries": url_for("entries"),
        "calendar": url_for("calendar_view"),
        "settings": url_for("settings_view"),
        "capsules": url_for("capsules.index"),
        "dashboard": url_for("dashboard"),
    }


@assistant_bp.route("/assistant")
@login_required
def page():
    return render_template(
        "assistant.html",
        user=current_user(),
        suggestions=SUGGESTIONS
    )


@assistant_bp.route("/api/assistant", methods=["POST"])
@login_required
def ask():
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON message."}), 400

    message = payload.get("message")

    if not isinstance(message, str):
        return jsonify({"error": "A text message is required."}), 400

    user = current_user()
    entries = [
        dict(row) for row in database.get_entries_by_user(user["id"])
    ]

    answer = respond(
        message,
        entries,
        database.get_local_time().date(),
        _urls()
    )

    return jsonify(answer)
