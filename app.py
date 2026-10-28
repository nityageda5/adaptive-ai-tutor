import os, json, sqlite3, datetime
import streamlit as st
import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    try:
        API_KEY = st.secrets["GEMINI_API_KEY"]
    except Exception:
        API_KEY = None
client = genai.Client(api_key=API_KEY)
MODEL = "gemini-3.5-flash-lite"

conn = sqlite3.connect("progress.db", check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS attempts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT, topic TEXT, level TEXT,
    score INTEGER, weak_areas TEXT, date TEXT)""")
conn.commit()


def save_attempt(user, topic, level, score, weak):
    conn.execute(
        "INSERT INTO attempts(user,topic,level,score,weak_areas,date) VALUES(?,?,?,?,?,?)",
        (user, topic, level, score, ", ".join(weak), datetime.datetime.now().strftime("%d-%m %H:%M")),
    )
    conn.commit()


def get_level(user, topic):
    row = conn.execute(
        "SELECT score FROM attempts WHERE user=? AND topic=? ORDER BY id DESC LIMIT 1",
        (user, topic.lower()),
    ).fetchone()
    if row is None:
        return "Easy"
    if row[0] <= 2:
        return "Easy"
    if row[0] <= 4:
        return "Medium"
    return "Hard"


def get_weak(user, topic):
    row = conn.execute(
        "SELECT weak_areas FROM attempts WHERE user=? AND topic=? ORDER BY id DESC LIMIT 1",
        (user, topic.lower()),
    ).fetchone()
    return row[0] if row and row[0] else ""


def clean_json(text):
    return text.replace("```json", "").replace("```", "").strip()


def generate_quiz(topic, level, focus=""):
    focus_line = f"Make at least 3 questions about these weak concepts: {focus}." if focus else ""
    prompt = f"""Create 5 multiple choice questions on "{topic}" at {level} difficulty. {focus_line}
Return ONLY a JSON list. Each item must have:
"question": string,
"options": list of exactly 4 strings,
"answer": integer index 0-3 of the correct option,
"subtopic": short string naming the concept tested."""
    for _ in range(2):
        try:
            resp = client.models.generate_content(model=MODEL, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", thinking_config=types.ThinkingConfig(thinking_level="low")))
            data = json.loads(clean_json(resp.text))
            assert len(data) >= 1
            return data
        except Exception as e:
            st.session_state.last_err = str(e)
            continue
    return None


def explain_gaps(topic, weak, level):
    prompt = f"""A student is learning "{topic}" ({level} level) and is weak in: {', '.join(weak)}.
Write in simple English (short):
1. A friendly simple explanation of each weak concept with one real-life example.
2. Then 3 practice questions (no answers) to try next.
3. End with a "Next steps" section: 2-3 short actionable tips (what to revise, how long, what to do next).
Use markdown."""
    try:
        return client.models.generate_content(model=MODEL, contents=prompt).text
    except Exception as e:
        return f"AI error: {e}"


st.set_page_config(page_title="AI Adaptive Tutor", page_icon="🧠")
st.title("🧠 The AI That Learns How You Learn")

user = st.sidebar.text_input("Your name", "Student")
topic = st.sidebar.text_input("Topic", "SQL Joins")
level = get_level(user, topic)
st.sidebar.info(f"Current level: **{level}**")

tab1, tab2, tab3 = st.tabs(["📝 Learn", "📈 Progress", "💬 Ask Doubt"])

with tab1:
    if st.button("Start / Retest quiz"):
        if not topic.strip():
            st.warning("Pehle topic likho.")
        else:
            with st.spinner("Quiz ban raha hai..."):
                quiz = generate_quiz(topic, level, get_weak(user, topic))
            if quiz is None:
                st.error("AI se quiz nahi mila: " + st.session_state.get("last_err", ""))
            else:
                st.session_state.quiz = quiz
                st.session_state.level = level
                st.session_state.result = None
                for i in range(20):
                    st.session_state.pop(f"q{i}", None)

    quiz = st.session_state.get("quiz")
    if quiz and st.session_state.get("result") is None:
        for i, q in enumerate(quiz):
            st.radio(f"Q{i+1}. {q['question']}", q["options"], index=None, key=f"q{i}")
        if st.button("Submit answers"):
            answers = [st.session_state.get(f"q{i}") for i in range(len(quiz))]
            if None in answers:
                st.warning("Saare sawaalon ke jawab do.")
            else:
                score, weak = 0, []
                for q, a in zip(quiz, answers):
                    if q["options"].index(a) == q["answer"]:
                        score += 1
                    else:
                        weak.append(q["subtopic"])
                weak = list(dict.fromkeys(weak))
                save_attempt(user, topic.lower(), st.session_state.level, score, weak)
                with st.spinner("Tumhare gaps analyze ho rahe hain..."):
                    plan = explain_gaps(topic, weak, st.session_state.level) if weak else None
                st.session_state.result = {"score": score, "total": len(quiz), "weak": weak, "plan": plan}
                st.rerun()

    res = st.session_state.get("result")
    if res:
        st.subheader(f"Score: {res['score']}/{res['total']}")
        if res["weak"]:
            st.error("Weak areas: " + ", ".join(res["weak"]))
            st.markdown("### 📚 Your personalized learning path")
            st.markdown(res["plan"])
            st.info("Ab padho, phir 'Start / Retest quiz' dabao. Difficulty automatically adapt hogi.")
        else:
            st.success("Perfect! Koi weak area nahi. Retest karo, level badh jayega 🚀")

with tab2:
    df = pd.read_sql_query(
        "SELECT topic, level, score, weak_areas, date FROM attempts WHERE user=? ORDER BY id",
        conn, params=(user,),
    )
    if df.empty:
        st.write("Abhi koi attempt nahi hai.")
    else:
        df["attempt"] = range(1, len(df) + 1)
        st.line_chart(df.set_index("attempt")["score"])
        st.dataframe(df[["date", "topic", "level", "score", "weak_areas"]])


def answer_doubt(user, topic, question, history):
    weak = get_weak(user, topic)
    past = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    prompt = f"""You are a friendly personal tutor. The student is learning "{topic}".
Their weak areas: {weak or 'not known yet'}.
Recent chat:
{past}

Student's doubt: {question}

Answer in simple English, short (max 120 words), with one easy example.
End with one small follow-up question to check understanding."""
    try:
        return client.models.generate_content(model=MODEL, contents=prompt).text
    except Exception as e:
        return f"AI error: {e}"


with tab3:
    st.subheader("💬 Koi doubt hai? Pucho!")
    st.caption(f"Topic: {topic}")
    if "chat" not in st.session_state:
        st.session_state.chat = []
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    q = st.chat_input("Apna doubt likho...")
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.spinner("Soch raha hoon..."):
            ans = answer_doubt(user, topic, q, st.session_state.chat)
        st.session_state.chat.append({"role": "user", "content": q})
        st.session_state.chat.append({"role": "assistant", "content": ans})
        with st.chat_message("assistant"):
            st.markdown(ans)
