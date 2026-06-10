import os
from datetime import datetime
import pytz

# ============ BOT CONFIG ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "5341425626"))

# ============ GEMINI CONFIG ============
GEMINI_KEYS = [k.strip() for k in os.getenv("GEMINI_KEYS", "").split(",") if k.strip()]
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MAX_TOKENS = 8192      # ADD THIS
GEMINI_TEMPERATURE = 0.5       # ADD THIS

# ============ CF WORKER PROXY ============
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://atlas-bot-proxy.hamza818483.workers.dev")
BASE_URL = "https://atlas-bot-proxy.hamza818483.workers.dev"
# ============ SUPABASE CONFIG ============
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wbdyjpjbczfunyhhmtry.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ============ DEFAULTS ============
DEFAULT_TIMER = 35  # seconds per question
NEW_PRACTICE_COUNT = 15
DEFAULT_FREE_LIMIT = 5
DEFAULT_DAILY_LIMIT = 50
DEFAULT_NEGATIVE_MARK = -0.25
MAX_MCQ = 35
MIN_MCQ = 15

# ============ BANGLADESH TIME ============
BD_TZ = pytz.timezone("Asia/Dhaka")

# ============ FLASK SERVER ============
FLASK_PORT = 7860
FLASK_HOST = "0.0.0.0"
# ============ EXAM LINK EXPIRY ============
EXAM_EXPIRY_SECONDS = None  # 1 hour

# ============ POLL DELAY ============
POLL_DELAY = 1.5  # seconds between polls

# ============ QUIZ TIMER ============
QUIZ_TIMER = int(os.getenv("QUIZ_TIMER", "35"))  # can be changed via /settimer

# ============ STORAGE ============
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ============ PDF STORAGE ============
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
os.makedirs(PDF_DIR, exist_ok=True)

# ============ LOGGING ============
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"bot_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")

# ============ PROCESSING MESSAGE ============
PROCESSING_MSG = """
Assalamu Alaikum 🌙
Atlas এ আপনাকে স্বাগতম, dear {first_name}!

📝 Attempt No: {attempt}/{limit}

MCQ poll, quiz, exam link বানানো হচ্ছে ⚙️
অনুগ্রহ করে অপেক্ষা করুন...

⏱️ আনুমানিক সময়: ~{eta} সেকেন্ড
🕐 শেষ হবে: {end_time}
"""

# ============ PREMIUM MESSAGE ============
PREMIUM_MSG = """
ফিচারটি Unlimited ইউজ এর জন্য আপনাকে এটলাসের প্রিমিয়াম প্যাকেজ নিতে হবে।

✅ ফুল ব্যাচ, এক্সাম ব্যাচের জন্য ফ্রী

⚡ প্রিমিয়াম প্যাকেজ নিতে যোগাযোগ করুন এডমিনের সাথে...
🌟 Telegram: @rafi_somc
🔗 Whatsapp: wa.me/8801999681290
"""

# ============ GEMINI SYSTEM PROMPT ============
GEMINI_PROMPT = """MCQ TYPE: Standard Easy

🟥 Overall Instructions:
- Image এ আগে থেকে MCQ বানানো থাকুক বা Information থাকুক, সকল জায়গা থেকেই প্রশ্ন বানাবে
- কোনো টেক্সটের নিচে কালার মার্ক বা কোনো টেক্সট হাইলাইটেড থাকলে সেখান থেকে প্রশ্ন বানান মিস দেওয়া যাবে না (must priority)
- কোয়ালিটিফুল প্রশ্ন বানাতে হবে।
- এমনভাবে সকল প্রশ্ন বানাবে যাতে সকল লাইন থেকে MCQ কিভাবে আসতে পারে আইডিয়া হয়ে যাবে।
- ছক থাকলে স্পেশাল প্রায়োরিটি পাবে (Use Every Information for Making MCQ)
- টপিকের নাম, অধ্যায়ের নাম, হেডলাইন, পেইজ সংখ্যা এসব info থেকে mcq banabe na.
- হাবিজাবি MCQ বানানো যাবে না, বেশি প্রশ্ন বানানোর প্রয়োজনে একটি MCQ কেই ঘুরিয়ে ফিরিয়ে দেওয়া যেতে পারে।
- সর্বনিম্ন ১৫ থেকে ৩৫ টি MCQ বানাতে হবে। তথ্য একবারেই না থাকলে ১০/১০+ MCQ

💥 প্রশ্ন: (ছোট, ১/১.৫/২ লাইন)
- সোর্স থেকে সকল টাইপের প্রশ্ন বানাতে হবে
- যতভাবে প্রশ্ন আসতে পারে প্রশ্ন রেডি হবে
- প্রশ্নগুলো মানসম্মত হবে
- প্রশ্ন কঠিন হবে না।

💥 অপশন: (৪টি, এক শব্দের ছোট+20% বড় অপশন)
- নির্দিষ্ট টপিক বা বক্স থেকে ৪ টা অপশন বানানো সীমাবদ্ধ থাকবে না, ইনপুট সোর্স থেকে মিক্সড তথ্যের অপশন থাকবে।
- অবশ্যই প্রশ্ন অনুযায়ী সঠিক তথ্যের অপশন বানাতে হবে।
- সোর্স অনুযায়ী বিভিন্ন অপশনে মিক্সড তথ্য থাকলেও সমস্যা নাই।
- যে টপিক/অংশ থেকে প্রশ্ন বানাবে সেখানে কাছাকাছি অপশন থাকলে সেখান থেকেই অপশন নিবে (High Priority), যাতে করে User Confused হয় কোনটা আন্সার হবে ভাবতে গিয়ে।
- অপশনে সঠিক উত্তর অবশ্যই একটিই থাকবে, বাকিগুলো ভুল উত্তর।
- ৪ টি অপশনই তথ্য দ্বারা পরিপূর্ণ থাকবে Must. অর্থাৎ অপশনে হ্যাঁ, না, সত্য, মিথ্যা, জ্বী, না এসব টাইপ কথা থাকবে না।

💥 উত্তর: 
- A/B/C/D এর মধ্যে একটি
- একাধিক উত্তর যেনো সঠিক না হয় এই বিষয় সর্বাধিক গুরুত্ব দিতে হবে।
- Answer গুলো different Option এ হতে হবে must.

💥 ব্যাখ্যা: 
- সঠিক উত্তর + ওই টপিকে রিলেটেড বাকি তথ্য (Source থেকে) থাকবে, যাতে একটা MCQ Solve করতে গিয়ে ইউজার ব্যাখ্যা দেখে আরো কয়েকটা তথ্য শিখার মাধ্যমে জ্ঞান অর্জন করতে পারে।
- Input source থেকেই সব তথ্য
- Bengali explanation, max 200 character

💥 Output Format:
Return ONLY valid JSON. No other text before or after.
{
  "mcqs": [
    {
      "question": "প্রশ্ন টেক্সট",
      "options": ["অপশন ক", "অপশন খ", "অপশন গ", "অপশন ঘ"],
      "answer": 0,
      "explanation": "ব্যাখ্যা টেক্সট (max 200 chars)"
    }
  ]
"""
ATLAS MCQ BOT - Configuration (v2.0)
====================================
All env config, defaults, prompt types/defaults, caption templates,
command lists, ayats, feedback messages, and CF Worker proxy bases.
"""

import os
from datetime import datetime
import pytz

# ============ BOT CONFIG ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "5341425626"))

# ============ GEMINI CONFIG ============
GEMINI_KEYS = [k.strip() for k in os.getenv("GEMINI_KEYS", "").split(",") if k.strip()]
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MAX_TOKENS = 8192
GEMINI_TEMPERATURE = 0.4

# ============ CF WORKER PROXY ============
# HF Space cannot reach api.telegram.org directly — all calls go through the worker.
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "https://atlas-bot-proxy.hamza818483.workers.dev")
BASE_URL = CF_WORKER_URL
# python-telegram-bot base_url / base_file_url (note the trailing /bot and /file/bot)
CF_BOT_BASE = f"{CF_WORKER_URL}/bot"
CF_FILE_BASE = f"{CF_WORKER_URL}/file/bot"

# ============ PUBLIC EXAM / LINKS ============
HF_SPACE_URL = os.getenv("HF_SPACE_URL", "https://hamzaHF1-atlasbot.hf.space")
EXAM_BASE_URL = f"{HF_SPACE_URL}/exam"
ATLAS_WEBSITE = "https://Atlascourses.com"
ATLAS_YOUTUBE = "https://www.youtube.com/@atlasprep"

# ============ SUPABASE CONFIG ============
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wbdyjpjbczfunyhhmtry.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ============ DEFAULTS ============
DEFAULT_TIMER = 35           # seconds per question
NEW_PRACTICE_COUNT = 15
DEFAULT_FREE_LIMIT = 5
DEFAULT_DAILY_LIMIT = 50
DEFAULT_NEGATIVE_MARK = -0.25
MAX_MCQ = 35
MIN_MCQ = 15
MIN_TEXT_LEN = 60            # below this (or single line) → "no proper info" reply
MAX_NEW_EXAM = 5            # max "New Exam" regenerations per source (web)

# ============ BANGLADESH TIME ============
BD_TZ = pytz.timezone("Asia/Dhaka")

# ============ FLASK SERVER ============
FLASK_PORT = 7860
FLASK_HOST = "0.0.0.0"

# ============ POLL ============
POLL_DELAY = 1.5            # seconds between batch polls
QUIZ_TIMER = int(os.getenv("QUIZ_TIMER", "35"))

# ============ STORAGE / LOGS ============
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
os.makedirs(PDF_DIR, exist_ok=True)
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"bot_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")

# ============ COMMAND LISTS ============
# Shown to ALL users (default scope)
USER_COMMANDS = [
    ("start", "শুরু করুন"),
    ("all", "আপনার সব তৈরি MCQ"),
    ("bm", "বুকমার্ক প্রশ্নের PDF"),
]

# Shown ONLY to the admin (chat-scoped on /start)
ADMIN_COMMANDS = USER_COMMANDS + [
    ("send", "📢 সব ইউজারকে broadcast"),
    ("prompt", "🧠 Prompt edit/update"),
    ("info", "ইউজার রিপোর্ট"),
    ("permit", "ইউজার পারমিট"),
    ("limit", "ডেইলি লিমিট সেট"),
    ("free", "ফ্রি ট্রাই সেট"),
    ("daily", "পারমিটেড লিমিট"),
    ("setneg", "নেগেটিভ মার্ক"),
    ("settimer", "টাইমার সেট"),
    ("tag", "Quiz/Poll tag"),
    ("exp", "Explanation suffix"),
    ("log", "এরর লগ"),
]

# ============ MCQ TYPE OPTIONS (the 4 buttons) ============
# (db_key, button_label) — db_key maps to a prompt stored in atlas_settings as prompt_<key>
PROMPT_TYPES = [
    ("medical", "1️⃣ Medical Standard MCQ"),
    ("truefalse", "2️⃣ সত্য-মিথ্যার প্রশ্ন"),
    ("hard", "3️⃣ কঠিন প্রশ্ন"),
    ("mixed", "4️⃣ Mixed (1+2+3)"),
]

# ============ WELCOME CAPTION (under pinned source image) ============
WELCOME_CAPTION = """🌟 স্বাগতম প্রিয় শিক্ষার্থী {first_name}..!
🚀 Today Practice No: {practice_no}
✅ Total MCQ: {count}

🚀 Motivational line for you:
{ayat}"""

# ============ PROCESSING MESSAGE ============
PROCESSING_MSG = """Assalamu Alaikum 🌙
ATLAS — dear {first_name}!

🎯 Style: {style}
📝 Attempt No: {attempt}/{limit}

MCQ poll, quiz, exam link বানানো হচ্ছে ⚙️
অনুগ্রহ করে অপেক্ষা করুন...

⏱️ আনুমানিক সময়: ~{eta} সেকেন্ড
🕐 শেষ হবে: {end_time}"""

# ============ NO-INFO (short text) MESSAGE ============
NO_INFO_MSG = """দু:খিত! 😕
আপনার Text এ Proper info নেই! আরো তথ্য দিন, আমি MCQ Practice Tool বানিয়ে দিবো 😃"""

# ============ PREMIUM MESSAGE ============
PREMIUM_MSG = """ফিচারটি Unlimited ইউজ এর জন্য আপনাকে এটলাসের প্রিমিয়াম প্যাকেজ নিতে হবে।

✅ ফুল ব্যাচ, এক্সাম ব্যাচের জন্য ফ্রী

⚡ প্রিমিয়াম প্যাকেজ নিতে যোগাযোগ করুন এডমিনের সাথে...
🌟 Telegram: @rafi_somc
🔗 Whatsapp: wa.me/8801999681290"""

# ============================================
# DEFAULT PROMPTS (seeded into DB once; editable via /prompt)
# ============================================

# ---- Shared output-format block reused by every prompt ----
_OUTPUT_FORMAT = """
💥 Output Format:
Return ONLY valid JSON. No other text before or after.
{
  "mcqs": [
    {
      "question": "প্রশ্ন টেক্সট",
      "options": ["অপশন ক", "অপশন খ", "অপশন গ", "অপশন ঘ"],
      "answer": 0,
      "explanation": "ব্যাখ্যা টেক্সট (max 200 chars)"
    }
  ]
}
- answer: 0 for A, 1 for B, 2 for C, 3 for D
- Bengali + English Unicode; Math/Chemical → Unicode superscript/subscript
- No Markdown/HTML in questions/options
- Return ONLY JSON
"""

# ---- Prompt 1: Medical Standard (the original active prompt) ----
PROMPT_MEDICAL = """MCQ TYPE: Medical Standard

🟥 Overall Instructions:
- Image এ আগে থেকে MCQ বানানো থাকুক বা Information থাকুক, সকল জায়গা থেকেই প্রশ্ন বানাবে
- কোনো টেক্সটের নিচে কালার মার্ক বা হাইলাইটেড থাকলে সেখান থেকে প্রশ্ন মিস দেওয়া যাবে না (must priority)
- কোয়ালিটিফুল প্রশ্ন বানাতে হবে।
- এমনভাবে সকল প্রশ্ন বানাবে যাতে সকল লাইন থেকে MCQ আসতে পারে।
- ছক থাকলে স্পেশাল প্রায়োরিটি পাবে (Use Every Information)
- টপিকের নাম, অধ্যায়ের নাম, হেডলাইন, পেইজ সংখ্যা থেকে mcq বানাবে না।
- হাবিজাবি MCQ বানানো যাবে না।
- সর্বনিম্ন ১৫ থেকে ৩৫ টি MCQ বানাতে হবে। তথ্য কম থাকলে ১০+ MCQ

💥 প্রশ্ন: (ছোট, ১/১.৫/২ লাইন), মানসম্মত, খুব কঠিন নয়
💥 অপশন: (৪টি) — কাছাকাছি confusing অপশন High Priority; ৪টিই তথ্যপূর্ণ; হ্যাঁ/না/সত্য/মিথ্যা টাইপ নয়; সঠিক উত্তর একটিই
💥 উত্তর: A/B/C/D এর একটি; একাধিক সঠিক যেন না হয়; answer different option এ ছড়ানো
💥 ব্যাখ্যা: সঠিক উত্তর + ঐ টপিকের রিলেটেড আরও তথ্য (source থেকে); Bengali, max 200 chars
""" + _OUTPUT_FORMAT

# ---- Prompt 2: True/False style ----
PROMPT_TRUEFALSE = """MCQ TYPE: সত্য-মিথ্যা যাচাই (Statement-based)

🟥 Overall Instructions:
- Source এর প্রতিটি তথ্যকে statement আকারে রূপান্তর করে সত্য/মিথ্যা যাচাইয়ের প্রশ্ন বানাবে।
- প্রশ্নে কয়েকটি statement (i, ii, iii) দিয়ে "নিচের কোনটি সঠিক / কোনটি ভুল" ধরনের প্রশ্ন করবে।
- অপশন হবে statement-combination ভিত্তিক (যেমন: শুধু i ও ii সঠিক / সবগুলো সঠিক / কেবল iii ভুল ইত্যাদি)।
- টপিক/হেডলাইন/পেইজ নম্বর থেকে প্রশ্ন নয়।
- সর্বনিম্ন ১৫ থেকে ৩৫ টি; তথ্য কম হলে ১০+

💥 প্রশ্ন: statement-গুলো স্পষ্ট, ছোট; "কোনটি সঠিক/ভুল?" আকারে
💥 অপশন: (৪টি) statement combination; ঠিক একটিই সঠিক; confusing হবে
💥 উত্তর: A/B/C/D এর একটি
💥 ব্যাখ্যা: কোন statement কেন সত্য/মিথ্যা তা সংক্ষেপে; Bengali, max 200 chars
""" + _OUTPUT_FORMAT

# ---- Prompt 3: Hard / higher-order ----
PROMPT_HARD = """MCQ TYPE: কঠিন প্রশ্ন (Higher Order Thinking)

🟥 Overall Instructions:
- Source এর তথ্য ব্যবহার করে analytical, application ও reasoning-ভিত্তিক কঠিন প্রশ্ন বানাবে।
- সরাসরি তথ্য জিজ্ঞেস না করে — কারণ, সম্পর্ক, তুলনা, ব্যতিক্রম, "নিচের কোনটি সঠিক নয়" ধরনের প্রশ্ন।
- একাধিক তথ্য মিলিয়ে চিন্তা করতে হয় এমন প্রশ্ন প্রায়োরিটি।
- টপিক/হেডলাইন/পেইজ নম্বর থেকে প্রশ্ন নয়।
- সর্বনিম্ন ১৫ থেকে ৩৫ টি; তথ্য কম হলে ১০+

💥 প্রশ্ন: কঠিন, চিন্তাশীল, ১-২ লাইন
💥 অপশন: (৪টি) খুব কাছাকাছি ও বিভ্রান্তিকর; ঠিক একটিই সঠিক; ৪টিই তথ্যপূর্ণ
💥 উত্তর: A/B/C/D এর একটি; answer different option এ ছড়ানো
💥 ব্যাখ্যা: কেন এটি সঠিক এবং বাকিগুলো কেন ভুল; Bengali, max 200 chars
""" + _OUTPUT_FORMAT

# ---- Prompt 4: Mixed (1+2+3) ----
PROMPT_MIXED = """MCQ TYPE: Mixed (Medical Standard + সত্য-মিথ্যা + কঠিন)

🟥 Overall Instructions:
- উপরের তিন ধরনের প্রশ্ন (Medical Standard, Statement সত্য-মিথ্যা, এবং কঠিন/HOTS) মিশিয়ে একটি ভারসাম্যপূর্ণ সেট বানাবে।
- আনুমানিক বণ্টন: ~৪০% standard, ~৩০% statement-based, ~৩০% কঠিন।
- Source এর সব তথ্য কাজে লাগাবে; ছক/হাইলাইট High Priority।
- টপিক/হেডলাইন/পেইজ নম্বর থেকে প্রশ্ন নয়।
- সর্বনিম্ন ১৫ থেকে ৩৫ টি; তথ্য কম হলে ১০+

💥 প্রতিটি প্রশ্ন তার ধরন অনুযায়ী মানসম্মত হবে; ৪টি তথ্যপূর্ণ অপশন; ঠিক একটিই সঠিক
💥 উত্তর: A/B/C/D এর একটি
💥 ব্যাখ্যা: সঠিক উত্তর + রিলেটেড তথ্য; Bengali, max 200 chars
""" + _OUTPUT_FORMAT

# Map db_key → default prompt text (used to seed DB on first run)
DEFAULT_PROMPTS = {
    "medical": PROMPT_MEDICAL,
    "truefalse": PROMPT_TRUEFALSE,
    "hard": PROMPT_HARD,
    "mixed": PROMPT_MIXED,
}

# Backwards-compat: some modules import GEMINI_PROMPT as a fallback
GEMINI_PROMPT = PROMPT_MEDICAL

# ============================================
# ISLAMIC AYATS — full pool (used in quiz/exam results)
# ============================================
AYATS = [
    "পড়ো তোমার প্রভুর নামে যিনি সৃষ্টি করেছেন... (সূরা আলাক: ১)",
    "যে ব্যক্তি জ্ঞানের সন্ধানে বের হয়, আল্লাহ তার জন্য জান্নাতের পথ সহজ করে দেন... (সহীহ মুসলিম)",
    "আল্লাহ জ্ঞানীদের মর্যাদা বৃদ্ধি করে দেন... (সূরা মুজাদালা: ১১)",
    "বলো, হে আমার প্রতিপালক! আমার জ্ঞান বৃদ্ধি করে দিন... (সূরা ত্বহা: ১১৪)",
    "তোমরা কি জানে তাদের সমান হতে পারো যারা জানে না?... (সূরা যুমার: ৯)",
    "জ্ঞানীরা আল্লাহকে বেশি ভয় করে... (সূরা ফাতির: ২৮)",
    "জ্ঞান অর্জন করা প্রত্যেক মুসলিম নর-নারীর উপর ফরজ... (ইবনে মাজাহ)",
    "দোলনা থেকে কবর পর্যন্ত জ্ঞান অর্জন করো... (হাদিস)",
    "নিশ্চয়ই আল্লাহ ধৈর্যশীলদের সাথে আছেন... (সূরা বাকারা: ১৫৩)",
    "হে ঈমানদারগণ! ধৈর্য ও নামাজের মাধ্যমে সাহায্য প্রার্থনা করো... (সূরা বাকারা: ১৫৩)",
    "ধৈর্য ধারণ করো, তোমার ধৈর্য আল্লাহরই সাহায্যে... (সূরা নাহল: ১২৭)",
    "নিশ্চয়ই ধৈর্যশীলদের অগণিত পুরস্কার দেওয়া হবে... (সূরা যুমার: ১০)",
    "আল্লাহ ধৈর্যশীলদের ভালোবাসেন... (সূরা আলে ইমরান: ১৪৬)",
    "সবর করো, আল্লাহর ওয়াদা সত্য... (সূরা রূম: ৬০)",
    "যারা ধৈর্য ধারণ করে এবং তাদের প্রতিপালকের উপর ভরসা করে... (সূরা আনকাবুত: ৫৯)",
    "তোমরা আল্লাহর রহমত থেকে নিরাশ হয়ো না... (সূরা যুমার: ৫৩)",
    "নিশ্চয়ই কষ্টের সাথেই স্বস্তি আছে... (সূরা ইনশিরাহ: ৫-৬)",
    "আল্লাহ কারো উপর তার সাধ্যের বাইরে বোঝা চাপান না... (সূরা বাকারা: ২৮৬)",
    "যে আল্লাহর উপর ভরসা করে, তার জন্য আল্লাহই যথেষ্ট... (সূরা তালাক: ৩)",
    "নিরাশ হয়ো না, আল্লাহ তোমাদের সাথে আছেন... (সূরা মুহাম্মদ: ৩৫)",
    "আল্লাহর রহমত সর্বব্যাপী... (সূরা আরাফ: ১৫৬)",
    "তোমরা হতাশ হয়ো না এবং দুঃখ করো না... (সূরা আলে ইমরান: ১৩৯)",
    "আল্লাহর সাহায্য নিকটেই... (সূরা বাকারা: ২১৪)",
    "মানুষ যা চেষ্টা করে, সে তাই পায়... (সূরা নাজম: ৩৯)",
    "আল্লাহর পথে যারা চেষ্টা করে, তিনি তাদের পথ দেখান... (সূরা আনকাবুত: ৬৯)",
    "তোমরা যদি আল্লাহর দ্বীনের সাহায্য করো, তিনি তোমাদের সাহায্য করবেন... (সূরা মুহাম্মদ: ৭)",
    "আল্লাহ সুন্দরভাবে কাজ করা ব্যক্তিদের ভালোবাসেন... (সূরা বাকারা: ১৯৫)",
    "যে আল্লাহকে ভয় করে, তিনি তার জন্য পথ তৈরি করে দেন... (সূরা তালাক: ২)",
    "তোমরা কল্যাণের কাজে পরস্পরকে সাহায্য করো... (সূরা মায়েদা: ২)",
    "আল্লাহর উপর ভরসা করো, কর্মে নিপুণ আল্লাহই যথেষ্ট... (সূরা আহযাব: ৩)",
    "আল্লাহই সর্বোত্তম ভরসাস্থল... (সূরা আলে ইমরান: ১৭৩)",
    "তোমার প্রতিপালকের নির্দেশে ধৈর্য ধারণ করো... (সূরা তূর: ৪৮)",
    "তোমরা আমাকে ডাকো, আমি তোমাদের ডাকে সাড়া দিবো... (সূরা মুমিন: ৬০)",
    "হে আমার প্রতিপালক! আমার বক্ষ প্রশস্ত করে দিন এবং আমার কাজ সহজ করে দিন... (সূরা ত্বহা: ২৫-২৬)",
    "হে আল্লাহ! যে জ্ঞান তুমি আমাকে দিয়েছো, তা দ্বারা আমাকে উপকৃত করো... (হাদিস)",
]

# Caption motivation pool (hotasha, dhoirjho, vorsha, tawakkul, ebadot, exam, chesta)
MOTIVATION_AYATS = AYATS

# ============ MARK-BASED FEEDBACK ============
FEEDBACKS = {
    "excellent": ["🌟 চমৎকার! আপনি দুর্দান্ত!", "🏆 অসাধারণ! আপনার প্রস্তুতি অনেক ভালো!", "💎 দারুণ! আপনি সেরা!"],
    "good": ["👍 ভালো! আরেকটু উন্নতি করতে হবে", "📈 ভালো করছেন! চালিয়ে যান", "👏 চমৎকার! এগিয়ে চলুন"],
    "average": ["📚 পড়ালেখা চালিয়ে যান, উন্নতি হবে", "🎯 মাঝামাঝি, আরও মনোযোগ দিন", "💪 ভালো চেষ্টা, আরও ভালো হবে"],
    "poor": ["💪 হাল ছাড়বেন না, আবার চেষ্টা করুন", "🌱 চেষ্টা চালিয়ে যান, সফল হবেন", "🔥 লেগে থাকুন, উন্নতি অবশ্যম্ভাবী"],
}
