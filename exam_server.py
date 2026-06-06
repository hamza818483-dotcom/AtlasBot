"""
ATLAS MCQ BOT - Exam Server (Flask)
Serves Website Exam page with full exam.html functionality
Direct copy from your exam.html - no shortcuts
"""

from flask import Flask, render_template_string, request, jsonify, make_response
import json
import uuid
from datetime import datetime, timedelta
from config import FLASK_PORT, FLASK_HOST, LOG_DIR, BD_TZ
import os

# ============================================
# LOGGING SETUP
# ============================================
LOG_FILE = os.path.join(LOG_DIR, f"exam_{datetime.now(BD_TZ).strftime('%Y-%m-%d')}.log")

def log(message, level="INFO"):
    timestamp = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] [EXAM] {message}"
    print(log_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_msg + "\n")

# ============================================
# FLASK APP
# ============================================
app = Flask(__name__)

# ============================================
# MCQ STORAGE (In-memory + database)
# ============================================
# quiz_id -> mcqs data
exam_store = {}

def store_exam(quiz_id, mcqs):
    """Store exam data"""
    exam_store[quiz_id] = {
        'mcqs': mcqs,
        'created_at': datetime.now(BD_TZ)
    }
    log(f"Exam stored: {quiz_id} with {len(mcqs)} questions")

def get_exam(quiz_id):
    """Get exam data"""
    return exam_store.get(quiz_id)

# ============================================
# ROUTES
# ============================================

@app.route('/health')
def health_check():
    """Health check endpoint for UptimeRobot"""
    return "OK", 200

@app.route('/exam/<quiz_id>')
def serve_exam(quiz_id):
    """Serve the website exam page"""
    log(f"Exam requested: {quiz_id}")
    
    exam_data = get_exam(quiz_id)
    if not exam_data:
        return "Exam not found or expired", 404
    
    mcqs = exam_data['mcqs']
    total = len(mcqs)
    
    # Generate exam HTML
    html = generate_exam_html(quiz_id, mcqs, total)
    return html

@app.route('/api/bookmark', methods=['POST'])
def save_bookmark():
    """API endpoint to save bookmark from exam page"""
    try:
        data = request.json
        phone = data.get('phone', 'anonymous')
        log(f"Bookmark request from: {phone}")
        
        # Import database module
        from database import add_bookmark
        
        result = add_bookmark(phone, {
            'question_text': data.get('question_text', ''),
            'option1': data.get('option1', ''),
            'option2': data.get('option2', ''),
            'option3': data.get('option3', ''),
            'option4': data.get('option4', ''),
            'option5': data.get('option5', ''),
            'answer_index': data.get('answer_index', 1),
            'explanation': data.get('explanation', ''),
            'exam_name': data.get('exam_name', 'ATLAS Exam'),
            'subject': data.get('subject', ''),
            'chapter': data.get('chapter', '')
        })
        
        if result:
            return jsonify({'success': True, 'message': 'Bookmark saved'})
        else:
            return jsonify({'success': False, 'message': 'Failed to save'}), 500
            
    except Exception as e:
        log(f"Bookmark API error: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

# ============================================
# EXAM HTML GENERATOR
# ============================================
def generate_exam_html(quiz_id, mcqs, total):
    """Generate complete exam HTML page"""
    
    mcqs_json = json.dumps(mcqs, ensure_ascii=False)
    
    html = f"""
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATLAS APP · এক্সাম</title>
    <style>
        /* ============================================
           ATLAS APP — Complete Styles
           Direct copy from exam.html + style.css
        ============================================ */
        
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=Noto+Sans+Bengali:wght@300;400;500;600;700;800&display=swap');
        
        /* CSS Variables - Light Mode */
        :root {{
            --bg: #F0F2F8;
            --bg-secondary: #FFFFFF;
            --bg-tertiary: #E8EBF5;
            --text: #1A1D2E;
            --text-secondary: #4A5270;
            --text-tertiary: #7A82A8;
            --accent: #5A5FE0;
            --accent-hover: #4349C8;
            --accent-light: #ECEEFF;
            --accent-glow: rgba(90, 95, 224, 0.18);
            --atlas-bg: #0E1225;
            --atlas-card: #141830;
            --atlas-text: #A8B4FF;
            --atlas-border: #5A5FE0;
            --atlas-glow: rgba(90, 95, 224, 0.32);
            --atlas-glow-strong: rgba(90, 95, 224, 0.55);
            --success: #0EA867;
            --success-light: #DCFBEE;
            --success-glow: rgba(14, 168, 103, 0.18);
            --error: #E53E3E;
            --error-light: #FEE8E8;
            --error-glow: rgba(229, 62, 62, 0.18);
            --warning: #D97706;
            --warning-light: #FEF5DC;
            --info: #2B7EDB;
            --info-light: #DBEEFF;
            --border: #D4D8EE;
            --border-focus: #5A5FE0;
            --divider: #E2E5F2;
            --overlay: rgba(10, 12, 30, 0.52);
            --shadow-sm: 0 1px 4px rgba(30, 35, 90, 0.08);
            --shadow: 0 2px 10px rgba(30, 35, 90, 0.11);
            --shadow-md: 0 4px 18px rgba(30, 35, 90, 0.14);
            --shadow-lg: 0 8px 34px rgba(30, 35, 90, 0.18);
            --shadow-glow: 0 0 0 3px var(--accent-glow);
            --radius: 16px;
            --radius-md: 12px;
            --radius-sm: 8px;
            --radius-full: 9999px;
            --card-bg: #FFFFFF;
            --card-hover: #F4F6FF;
            --option-bg: #F4F6FF;
            --option-border: #D4D8EE;
            --option-selected-bg: #ECEEFF;
            --option-selected-border: #5A5FE0;
            --btn-bg: #E8EBF8;
        }}
        
        /* Dark Mode */
        .dark-mode {{
            --bg: #080C1A;
            --bg-secondary: #0F1528;
            --bg-tertiary: #161D35;
            --text: #E8ECFF;
            --text-secondary: #8892C8;
            --text-tertiary: #555E88;
            --accent: #7B82FF;
            --accent-hover: #9CA3FF;
            --accent-light: #1A1E40;
            --accent-glow: rgba(123, 130, 255, 0.22);
            --atlas-bg: #0A0D1E;
            --atlas-card: #0F1428;
            --atlas-text: #A8B4FF;
            --atlas-border: #5A5FE0;
            --atlas-glow: rgba(123, 130, 255, 0.30);
            --atlas-glow-strong: rgba(123, 130, 255, 0.55);
            --success: #22D47A;
            --success-light: #052A18;
            --error: #F87171;
            --error-light: #2A0808;
            --warning: #FBBF24;
            --warning-light: #291A00;
            --border: #252E55;
            --border-focus: #7B82FF;
            --divider: #1E2645;
            --overlay: rgba(0, 0, 0, 0.78);
            --shadow-sm: 0 1px 4px rgba(0, 0, 0, 0.45);
            --shadow: 0 2px 10px rgba(0, 0, 0, 0.55);
            --shadow-md: 0 4px 18px rgba(0, 0, 0, 0.65);
            --shadow-lg: 0 8px 34px rgba(0, 0, 0, 0.75);
            --shadow-glow: 0 0 0 3px var(--accent-glow);
            --card-bg: #0F1528;
            --card-hover: #161D35;
            --option-bg: #161D35;
            --option-border: #252E55;
            --option-selected-bg: #1A1E40;
            --option-selected-border: #7B82FF;
            --btn-bg: #1E2645;
        }}
        
        /* Global Reset */
        *, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html {{ scroll-behavior: smooth; -webkit-font-smoothing: antialiased; }}
        body {{
            font-family: 'Inter', 'Noto Sans Bengali', system-ui, sans-serif;
            background-color: var(--bg); color: var(--text);
            min-height: 100vh; overflow-x: hidden;
            padding-top: 56px; padding-bottom: 64px;
            line-height: 1.65;
            transition: background-color 0.3s ease, color 0.3s ease;
        }}
        
        /* Animations */
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        @keyframes pulseGlow {{ 0%,100%{{box-shadow:var(--shadow-md),0 0 24px var(--atlas-glow);}} 50%{{box-shadow:var(--shadow-lg),0 0 44px var(--atlas-glow-strong);}} }}
        @keyframes slideUp {{ from{{transform:translateY(100%)}} to{{transform:translateY(0)}} }}
        
        /* Header */
        .header {{
            position: fixed; top: 0; left: 0; right: 0; height: 56px;
            background-color: var(--card-bg); border-bottom: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between;
            padding: 0 16px; z-index: 1000; box-shadow: var(--shadow-sm);
            backdrop-filter: blur(14px);
        }}
        .header-left {{ display: flex; align-items: center; gap: 10px; }}
        .header-right {{ display: flex; align-items: center; gap: 8px; }}
        .header-icon {{
            background: var(--bg-tertiary); border: 1px solid var(--border);
            color: var(--text); width: 36px; height: 36px; border-radius: var(--radius-full);
            font-size: 16px; cursor: pointer; transition: all 0.2s ease;
            display: flex; align-items: center; justify-content: center;
        }}
        .header-icon:hover {{ background: var(--accent-light); border-color: var(--accent); color: var(--accent); }}
        
        /* ATLAS Brand */
        .atlas-brand-box {{
            display: inline-flex; align-items: center; gap: 8px;
            padding: 6px 14px 6px 8px; border-radius: 8px;
            background: linear-gradient(135deg, #5A5FE0 0%, #8B5CF6 100%);
            color: #fff;
        }}
        .dark-mode .atlas-brand-box {{ background: linear-gradient(135deg, #7B82FF 0%, #A78BFA 100%); }}
        .atlas-brand-text {{ font-size: 16px; font-weight: 800; letter-spacing: -0.5px; color: #fff; white-space: nowrap; }}
        
        /* Timer Sticky */
        .timer-sticky {{
            position: sticky; top: 56px; z-index: 100;
            background: var(--atlas-bg); border: 1px solid var(--atlas-border);
            border-radius: var(--radius-sm); padding: 10px 14px;
            display: flex; align-items: center; justify-content: space-between;
            margin: 0 14px 0; box-shadow: 0 2px 10px var(--atlas-glow);
        }}
        .timer-text {{ font-size: 18px; font-weight: 800; color: var(--atlas-text); letter-spacing: 2px; font-variant-numeric: tabular-nums; }}
        .timer-warning {{ color: var(--error)!important; animation: timerPulse 0.5s infinite; }}
        @keyframes timerPulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.5}} }}
        .progress-text {{ font-size: 12px; font-weight: 600; color: var(--atlas-text); opacity: 0.75; }}
        
        /* Progress Bar */
        .progress-bar-wrap {{ height: 4px; background: rgba(255,255,255,0.1); margin: 0 14px 12px; overflow: hidden; }}
        .progress-bar-fill {{ height: 100%; background: linear-gradient(90deg,var(--accent),#7c3aed); transition:width 0.4s ease; }}
        
        /* Questions Area */
        .questions-area {{ width: 100%; padding: 0 14px 20px; }}
        
        /* MCQ Card */
        .mcq-card {{
            background: var(--card-bg); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 16px; margin-bottom: 12px;
            box-shadow: var(--shadow-sm); width: 100%; animation: fadeIn 0.2s ease;
        }}
        .q-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
        .q-number {{ font-size: 11px; color: var(--accent); font-weight: 700; background: var(--accent-light); padding: 3px 9px; border-radius: var(--radius-full); }}
        .q-actions {{ display: flex; gap: 5px; }}
        .icon-btn {{ background: none; border: 1px solid var(--border); font-size: 14px; cursor: pointer; padding: 4px 7px; border-radius: 6px; transition: 0.2s; opacity: 0.7; color: var(--text-secondary); }}
        .icon-btn:hover {{ opacity: 1; border-color: var(--accent); }}
        .icon-btn.bookmark.active {{ opacity: 1; color: var(--warning); border-color: var(--warning); }}
        .q-text {{ font-size: 14px; font-weight: 600; margin-bottom: 12px; line-height: 1.7; color: var(--text); font-family: 'Noto Sans Bengali','Inter',sans-serif; }}
        .q-text img {{ max-width: 100%; border-radius: 8px; margin: 6px 0; }}
        
        /* Options */
        .option-item {{
            display: flex; align-items: center; gap: 10px;
            padding: 11px 13px; margin-bottom: 7px;
            background-color: var(--option-bg); border: 1.5px solid var(--option-border);
            border-radius: var(--radius-sm); cursor: pointer; transition: all 0.18s;
            font-size: 13px; font-family: 'Noto Sans Bengali','Inter',sans-serif; color: var(--text);
        }}
        .option-item:hover:not(.dimmed):not(.locked){{ border-color: var(--accent); background: var(--option-selected-bg); }}
        .option-item.selected {{ background-color: var(--option-selected-bg); border-color: var(--option-selected-border); cursor: default; }}
        .option-item.dimmed {{ opacity: 0.35; cursor: not-allowed; pointer-events: none; }}
        .option-item.correct-reveal {{ background-color: var(--success-light); border-color: var(--success); }}
        .option-item.wrong-reveal {{ background-color: var(--error-light); border-color: var(--error); }}
        .option-radio {{
            width: 20px; height: 20px; border-radius: 50%; border: 2px solid var(--text-tertiary);
            display: flex; align-items: center; justify-content: center; flex-shrink: 0;
            font-size: 11px; font-weight: 700; color: var(--text-tertiary); transition: all 0.18s;
        }}
        .option-item.selected .option-radio {{ border-color: var(--option-selected-border); background: var(--option-selected-border); color: #fff; }}
        .option-item.correct-reveal .option-radio {{ border-color: var(--success); background: var(--success); color: #fff; }}
        .option-item.wrong-reveal .option-radio {{ border-color: var(--error); background: var(--error); color: #fff; }}
        .option-text {{ flex: 1; line-height: 1.5; }}
        
        /* Submit Fixed */
        .submit-fixed {{
            position: fixed; bottom: 0; left: 0; right: 0;
            background: var(--success); color: #fff; border: none;
            padding: 14px; font-size: 16px; font-weight: 700;
            cursor: pointer; z-index: 100; box-shadow: 0 -2px 16px var(--success-glow);
            transition: all 0.2s; font-family: 'Noto Sans Bengali','Inter',sans-serif;
            text-align: center;
        }}
        .submit-fixed:hover {{ filter: brightness(0.9); }}
        
        /* Nav FAB */
        .nav-fab {{
            position: fixed; right: 0; top: 50%; transform: translateY(-50%);
            width: 38px; height: 38px; background: var(--accent); border: none;
            border-radius: 10px 0 0 10px; color: #fff; font-size: 15px;
            font-weight: 700; cursor: pointer; z-index: 101;
            box-shadow: 0 4px 12px var(--accent-glow);
            display: flex; align-items: center; justify-content: center;
        }}
        
        /* Nav Popup */
        .nav-overlay {{
            position: fixed; inset: 0; background: var(--overlay); z-index: 200;
            display: none; align-items: flex-end;
        }}
        .nav-overlay.active {{ display: flex; }}
        .nav-popup {{
            width: 100%; max-width: 520px; margin: 0 auto;
            background: var(--card-bg); border-radius: var(--radius) var(--radius) 0 0;
            padding: 16px; border: 1px solid var(--border);
            animation: slideUp 0.2s; max-height: 80vh; overflow-y: auto;
        }}
        .nav-popup-title {{ font-size: 14px; font-weight: 700; color: var(--accent); margin-bottom: 12px; text-align: center; font-family: 'Noto Sans Bengali','Inter',sans-serif; }}
        .nav-grid {{ display: grid; grid-template-columns: repeat(8,1fr); gap: 5px; }}
        @media(max-width:480px){{ .nav-grid{{grid-template-columns: repeat(6,1fr);}} }}
        .nav-num {{
            aspect-ratio: 1; border-radius: 6px;
            background: var(--option-bg); border: 1px solid var(--border);
            color: var(--text-secondary); font-size: 11px; font-weight: 600;
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            transition: all 0.2s;
        }}
        .nav-num.answered {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
        .nav-num.correct {{ background: var(--success); border-color: var(--success); color: #fff; }}
        .nav-num.wrong {{ background: var(--error); border-color: var(--error); color: #fff; }}
        .nav-stats {{ text-align: center; margin-top: 8px; font-size: 10px; color: var(--text-secondary); }}
        .nav-close {{ display: block; margin: 12px auto 0; padding: 8px 20px; background: var(--accent); color: #fff; border: none; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; font-family: 'Noto Sans Bengali','Inter',sans-serif; }}
        
        /* Result */
        .result-wrap {{ max-width: 600px; margin: 0 auto; padding: 14px 14px 90px; animation: fadeIn 0.3s; }}
        .result-score-card {{
            background: linear-gradient(140deg,var(--atlas-bg) 0%,var(--atlas-card) 100%);
            border: 1.5px solid var(--atlas-border); border-radius: var(--radius);
            padding: 24px 18px; text-align: center; margin-bottom: 14px;
            box-shadow: var(--shadow-md),0 0 24px var(--atlas-glow); width: 100%;
        }}
        .result-exam-name {{ font-size: 18px; font-weight: 800; color: #fff; margin-bottom: 12px; font-family: 'Noto Sans Bengali','Inter',sans-serif; }}
        .result-big-score {{ font-size: 42px; font-weight: 900; color: var(--atlas-text); text-shadow: 0 0 20px var(--atlas-glow-strong); }}
        .result-total {{ font-size: 14px; color: var(--atlas-text); opacity: 0.75; margin-top: 4px; }}
        .result-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; margin: 14px 0; }}
        .result-stat {{ background: var(--card-bg); border-radius: var(--radius-sm); padding: 12px 8px; text-align: center; border: 1px solid var(--border); width: 100%; }}
        .result-stat-val {{ font-size: 18px; font-weight: 800; }}
        .result-stat-label {{ font-size: 10px; color: var(--text-secondary); margin-top: 2px; font-family: 'Noto Sans Bengali','Inter',sans-serif; }}
        .correct-val {{ color: var(--success); }}
        .wrong-val {{ color: var(--error); }}
        .skipped-val {{ color: var(--warning); }}
        
        /* Action Buttons */
        .practice-actions {{ display: flex; gap: 8px; margin-bottom: 10px; justify-content: center; flex-wrap: wrap; }}
        .practice-btn {{
            flex: 1; min-width: 120px; padding: 12px;
            border-radius: var(--radius-sm); border: none;
            font-size: 13px; font-weight: 700; cursor: pointer;
            transition: all 0.2s; text-align: center;
            font-family: 'Noto Sans Bengali','Inter',sans-serif; color: #fff;
        }}
        .btn-practice-again {{ background: var(--accent); }}
        .btn-mistake-practice {{ background: var(--error); }}
        
        /* Scrollbar */
        ::-webkit-scrollbar {{ width: 5px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background-color: var(--border); border-radius: 3px; }}
        
        /* Confirmation Modal */
        .confirm-overlay {{
            position: fixed; inset: 0; background: var(--overlay);
            z-index: 500; display: none; align-items: center; justify-content: center;
        }}
        .confirm-overlay.active {{ display: flex; }}
        .confirm-modal {{
            background: var(--card-bg); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 20px; max-width: 400px;
            width: 94%; text-align: center; box-shadow: var(--shadow-lg);
            animation: fadeIn 0.2s;
        }}
        .confirm-title {{ font-size: 16px; font-weight: 700; color: var(--text); margin-bottom: 12px; font-family: 'Noto Sans Bengali','Inter',sans-serif; }}
        .confirm-stats {{ font-size: 13px; color: var(--text-secondary); margin-bottom: 16px; line-height: 1.8; }}
        .confirm-buttons {{ display: flex; gap: 8px; justify-content: center; }}
        .confirm-btn {{
            padding: 10px 20px; border-radius: var(--radius-sm); border: none;
            font-size: 13px; font-weight: 600; cursor: pointer;
            font-family: 'Noto Sans Bengali','Inter',sans-serif;
        }}
        .confirm-btn-back {{ background: var(--btn-bg); color: var(--text); border: 1px solid var(--border); }}
        .confirm-btn-submit {{ background: var(--success); color: #fff; }}
    </style>
</head>
<body class="dark-mode">

<!-- HEADER -->
<header class="header">
    <div class="header-left">
        <div class="atlas-brand-box">
            <span class="atlas-brand-text">ATLAS EXAM</span>
        </div>
    </div>
    <div class="header-right">
        <button class="header-icon" onclick="toggleTheme()" id="themeToggle">☀️</button>
    </div>
</header>

<!-- MAIN CONTENT -->
<main>
    <!-- TIMER STICKY -->
    <div class="timer-sticky" id="timerSticky">
        <span class="timer-text" id="timerText">--:--</span>
        <span class="progress-text" id="progressText">📈 0%</span>
    </div>
    
    <!-- PROGRESS BAR -->
    <div class="progress-bar-wrap"><div class="progress-bar-fill" id="progressBarFill" style="width:0%"></div></div>
    
    <!-- QUESTIONS AREA -->
    <div class="questions-area" id="questionsArea"></div>
    
    <!-- SUBMIT BUTTON -->
    <button class="submit-fixed" id="submitBtn" onclick="submitExam()">📤 Submit</button>
    
    <!-- NAV FAB -->
    <button class="nav-fab" id="navFab" onclick="openNavPopup()">📋</button>
</main>

<!-- NAV POPUP -->
<div class="nav-overlay" id="navOverlay" onclick="closeNavPopup()">
    <div class="nav-popup" onclick="event.stopPropagation()">
        <div class="nav-popup-title">📋 প্রশ্ন নেভিগেশন</div>
        <div class="nav-grid" id="navGrid"></div>
        <div class="nav-stats" id="navStats"></div>
        <button class="nav-close" onclick="closeNavPopup()">✕ বন্ধ</button>
    </div>
</div>

<!-- CONFIRMATION MODAL -->
<div class="confirm-overlay" id="confirmOverlay">
    <div class="confirm-modal">
        <div class="confirm-title">⚠️ নিশ্চিত করুন</div>
        <div class="confirm-stats" id="confirmStats"></div>
        <div class="confirm-buttons">
            <button class="confirm-btn confirm-btn-back" onclick="closeConfirm()">🔙 Back</button>
            <button class="confirm-btn confirm-btn-submit" onclick="confirmSubmit()">✅ Confirm</button>
        </div>
    </div>
</div>

<!-- RESULT SECTION (Hidden initially) -->
<div id="resultSection" style="display:none;">
    <div class="result-wrap" id="resultContent"></div>
</div>

<script>
    // ============================================
    // EXAM DATA
    // ============================================
    const QUIZ_ID = "{quiz_id}";
    const TOTAL_QUESTIONS = {total};
    const EXAM_DATA = {mcqs_json};
    
    // ============================================
    // STATE
    // ============================================
    let isDarkMode = true;
    let userAnswers = {{}};
    let bookmarkedQuestions = {{}};
    let examSeconds = TOTAL_QUESTIONS * 35;
    let totalSeconds = examSeconds;
    let examTimer = null;
    let isExamSubmitted = false;
    
    // ============================================
    // INIT
    // ============================================
    function initExam() {{
        renderAllQuestions();
        startTimer();
        updateProgress();
        loadBookmarks();
        document.getElementById('timerText').textContent = formatTime(examSeconds);
    }}
    
    // ============================================
    // RENDER ALL QUESTIONS
    // ============================================
    function renderAllQuestions() {{
        let html = '';
        EXAM_DATA.forEach((q, i) => {{
            const qNum = i + 1;
            const bmClass = bookmarkedQuestions[q.id] ? ' active' : '';
            html += `<div class="mcq-card" id="qCard${{i}}">
                <div class="q-header">
                    <span class="q-number">প্রশ্ন ${{qNum}}/${{TOTAL_QUESTIONS}}</span>
                    <div class="q-actions">
                        <button class="icon-btn bookmark${{bmClass}}" id="bmBtn${{i}}" title="বুকমার্ক" onclick="toggleBookmark(${{i}})">🔖</button>
                    </div>
                </div>
                <div class="q-text">${{q.question}}</div>`;
            
            const labels = ['ক', 'খ', 'গ', 'ঘ'];
            q.options.forEach((opt, oi) => {{
                html += `<div class="option-item" id="opt${{i}}_${{oi}}" onclick="selectOption(${{i}},${{oi}})" data-qindex="${{i}}">
                    <span class="option-radio">${{labels[oi]}}</span>
                    <span class="option-text">${{opt}}</span>
                </div>`;
            }});
            
            html += `</div>`;
        }});
        
        document.getElementById('questionsArea').innerHTML = html;
    }}
    
    // ============================================
    // SELECT OPTION
    // ============================================
    function selectOption(qIndex, oIndex) {{
        if (isExamSubmitted) return;
        
        const qid = qIndex;
        
        // Deselect all options for this question
        document.querySelectorAll(`[data-qindex="${{qIndex}}"]`).forEach(el => {{
            el.classList.remove('selected');
            el.classList.add('dimmed');
        }});
        
        // Select clicked option
        const selectedEl = document.getElementById(`opt${{qIndex}}_${{oIndex}}`);
        if (selectedEl) {{
            selectedEl.classList.add('selected');
            selectedEl.classList.remove('dimmed');
        }}
        
        // Store answer
        userAnswers[qid] = oIndex;
        updateProgress();
    }}
    
    // ============================================
    // TIMER
    // ============================================
    function startTimer() {{
        stopTimer();
        examTimer = setInterval(() => {{
            examSeconds--;
            document.getElementById('timerText').textContent = formatTime(examSeconds);
            
            if (examSeconds <= 60) {{
                document.getElementById('timerText').classList.add('timer-warning');
            }}
            
            if (examSeconds <= 0) {{
                submitExam(true);
            }}
        }}, 1000);
    }}
    
    function stopTimer() {{
        if (examTimer) {{
            clearInterval(examTimer);
            examTimer = null;
        }}
    }}
    
    function formatTime(seconds) {{
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${{String(m).padStart(2,'0')}}:${{String(s).padStart(2,'0')}}`;
    }}
    
    // ============================================
    // PROGRESS
    // ============================================
    function updateProgress() {{
        const answered = Object.keys(userAnswers).length;
        const pct = Math.round(answered / TOTAL_QUESTIONS * 100);
        document.getElementById('progressText').textContent = `📈 ${{pct}}%`;
        document.getElementById('progressBarFill').style.width = pct + '%';
    }}
    
    // ============================================
    // BOOKMARK
    // ============================================
    function loadBookmarks() {{
        try {{
            const saved = localStorage.getItem('atlas_exam_bookmarks');
            if (saved) bookmarkedQuestions = JSON.parse(saved);
        }} catch(e) {{}}
    }}
    
    function toggleBookmark(index) {{
        const q = EXAM_DATA[index];
        if (bookmarkedQuestions[index]) {{
            delete bookmarkedQuestions[index];
            document.getElementById('bmBtn' + index)?.classList.remove('active');
        }} else {{
            bookmarkedQuestions[index] = q;
            document.getElementById('bmBtn' + index)?.classList.add('active');
            
            // Save to Supabase via API
            fetch('/api/bookmark', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    phone: localStorage.getItem('atlas_phone') || 'anonymous',
                    question_text: q.question,
                    option1: q.options[0],
                    option2: q.options[1],
                    option3: q.options[2],
                    option4: q.options[3],
                    answer_index: q.answer,
                    explanation: q.explanation,
                    exam_name: 'ATLAS Exam',
                    subject: '',
                    chapter: ''
                }})
            }}).catch(e => console.log('Bookmark save failed:', e));
        }}
        
        localStorage.setItem('atlas_exam_bookmarks', JSON.stringify(bookmarkedQuestions));
    }}
    
    // ============================================
    // NAVIGATION
    // ============================================
    function openNavPopup() {{
        let html = '';
        EXAM_DATA.forEach((q, i) => {{
            let cls = 'nav-num';
            if (userAnswers[i] !== undefined) cls += ' answered';
            html += `<button class="${{cls}}" onclick="goToQuestion(${{i}});closeNavPopup();">${{i+1}}</button>`;
        }});
        
        document.getElementById('navGrid').innerHTML = html;
        document.getElementById('navStats').textContent = 
            `🟦 উত্তর: ${{Object.keys(userAnswers).length}} | ⬜ বাকি: ${{TOTAL_QUESTIONS - Object.keys(userAnswers).length}}`;
        document.getElementById('navOverlay').classList.add('active');
    }}
    
    function closeNavPopup() {{
        document.getElementById('navOverlay').classList.remove('active');
    }}
    
    function goToQuestion(index) {{
        const card = document.getElementById('qCard' + index);
        if (card) card.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}
    
    // ============================================
    // SUBMIT
    // ============================================
    function submitExam(isAutoSubmit) {{
        if (isExamSubmitted) return;
        if (!isAutoSubmit) {{
            showConfirmModal();
            return;
        }}
        processSubmit();
    }}
    
    function showConfirmModal() {{
        const answered = Object.keys(userAnswers).length;
        const skipped = TOTAL_QUESTIONS - answered;
        document.getElementById('confirmStats').innerHTML = 
            `✅ Answered: ${{answered}}<br>⏭️ Skipped: ${{skipped}}<br>⚠️ জমা দিলে আর পরিবর্তন করা যাবে না।`;
        document.getElementById('confirmOverlay').classList.add('active');
    }}
    
    function closeConfirm() {{
        document.getElementById('confirmOverlay').classList.remove('active');
    }}
    
    function confirmSubmit() {{
        document.getElementById('confirmOverlay').classList.remove('active');
        processSubmit();
    }}
    
    function processSubmit() {{
        isExamSubmitted = true;
        stopTimer();
        
        // Calculate scores
        let correct = 0, wrong = 0, skipped = 0;
        EXAM_DATA.forEach((q, i) => {{
            const ua = userAnswers[i];
            if (ua === undefined) skipped++;
            else if (ua === q.answer) correct++;
            else wrong++;
        }});
        
        const negMark = wrong * 0.25;
        const finalScore = correct - negMark;
        const timeTaken = totalSeconds - examSeconds;
        
        // Hide exam elements
        document.getElementById('timerSticky').style.display = 'none';
        document.getElementById('submitBtn').style.display = 'none';
        document.getElementById('navFab').style.display = 'none';
        document.querySelector('.questions-area').style.display = 'none';
        document.querySelector('.progress-bar-wrap').style.display = 'none';
        
        // Show result
        showResult(correct, wrong, skipped, negMark, finalScore, timeTaken);
        
        // Show answers
        revealAnswers();
    }}
    
    function revealAnswers() {{
        EXAM_DATA.forEach((q, i) => {{
            const ua = userAnswers[i];
            q.options.forEach((opt, oi) => {{
                const el = document.getElementById(`opt${{i}}_${{oi}}`);
                if (!el) return;
                
                if (oi === q.answer) {{
                    el.classList.add('correct-reveal');
                }}
                if (ua !== undefined && oi === ua && ua !== q.answer) {{
                    el.classList.add('wrong-reveal');
                }}
            }});
        }});
    }}
    
    function showResult(correct, wrong, skipped, negMark, finalScore, timeTaken) {{
        const examName = 'ATLAS Exam';
        let html = '';
        
        // Score Card
        html += `<div class="result-score-card">
            <div class="result-exam-name">📝 ${{examName}}</div>
            <div class="result-big-score">${{finalScore.toFixed(2)}}</div>
            <div class="result-total">/ ${{TOTAL_QUESTIONS}}</div>
        </div>`;
        
        // Stats Grid
        html += `<div class="result-grid">
            <div class="result-stat">
                <div class="result-stat-val correct-val">✅ ${{correct}}</div>
                <div class="result-stat-label">সঠিক</div>
            </div>
            <div class="result-stat">
                <div class="result-stat-val wrong-val">❌ ${{wrong}}</div>
                <div class="result-stat-label">ভুল</div>
            </div>
            <div class="result-stat">
                <div class="result-stat-val skipped-val">⏭️ ${{skipped}}</div>
                <div class="result-stat-label">স্কিপ</div>
            </div>
        </div>`;
        
        // Time & Negative
        html += `<div class="result-score-card" style="padding:12px;">
            <div class="info-row">
                <span>⏱️ Time: ${{formatTime(timeTaken)}}</span>
                <span>📊 Negative: -${{negMark.toFixed(2)}}</span>
            </div>
        </div>`;
        
        // Islamic Ayat (random)
        const ayats = [
            "পড়ো তোমার প্রভুর নামে যিনি সৃষ্টি করেছেন... (সূরা আলাক: ১)",
            "আল্লাহ ধৈর্যশীলদের সাথে আছেন... (সূরা বাকারা: ১৫৩)",
            "নিশ্চয়ই কষ্টের সাথেই স্বস্তি আছে... (সূরা ইনশিরাহ: ৫-৬)",
            "মানুষ যা চেষ্টা করে, সে তাই পায়... (সূরা নাজম: ৩৯)"
        ];
        const randomAyat = ayats[Math.floor(Math.random() * ayats.length)];
        
        html += `<div class="result-score-card" style="padding:16px; text-align:center;">
            <div style="font-size:14px; color:var(--atlas-text); line-height:1.8;">📖 "${{randomAyat}}"</div>
        </div>`;
        
        // Retake Button
        html += `<div class="practice-actions">
            <button class="practice-btn btn-practice-again" onclick="retakeExam()">🔄 Retake</button>
        </div>`;
        
        document.getElementById('resultContent').innerHTML = html;
        document.getElementById('resultSection').style.display = 'block';
        window.scrollTo(0, 0);
    }}
    
    function retakeExam() {{
        userAnswers = {{}};
        isExamSubmitted = false;
        examSeconds = totalSeconds;
        
        document.getElementById('timerSticky').style.display = 'flex';
        document.getElementById('submitBtn').style.display = 'block';
        document.getElementById('navFab').style.display = 'flex';
        document.querySelector('.questions-area').style.display = 'block';
        document.querySelector('.progress-bar-wrap').style.display = 'block';
        document.getElementById('resultSection').style.display = 'none';
        
        document.getElementById('timerText').classList.remove('timer-warning');
        renderAllQuestions();
        startTimer();
        updateProgress();
        window.scrollTo(0, 0);
    }}
    
    // ============================================
    // THEME TOGGLE
    // ============================================
    function toggleTheme() {{
        isDarkMode = !isDarkMode;
        document.body.classList.toggle('dark-mode', isDarkMode);
        document.getElementById('themeToggle').textContent = isDarkMode ? '☀️' : '🌙';
        localStorage.setItem('atlas-exam-theme', isDarkMode ? 'dark' : 'light');
    }}
    
    // ============================================
    // LOAD THEME
    // ============================================
    if (localStorage.getItem('atlas-exam-theme') === 'light') {{
        isDarkMode = false;
        document.body.classList.remove('dark-mode');
        document.getElementById('themeToggle').textContent = '🌙';
    }}
    
    // ============================================
    // START EXAM
    // ============================================
    initExam();
</script>

</body>
</html>
"""
    return html

# ============================================
# STORE EXAM DATA (Called from bot.py)
# ============================================
def create_exam_link(quiz_id, mcqs):
    """Store exam and return URL"""
    store_exam(quiz_id, mcqs)
    return f"https://hamzaHF1-atlasbot.hf.space/exam/{quiz_id}"

# ============================================
# START SERVER
# ============================================
def run_exam_server():
    """Run Flask server"""
    log(f"🚀 Exam server starting on {FLASK_HOST}:{FLASK_PORT}")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False)

if __name__ == '__main__':
    run_exam_server()
