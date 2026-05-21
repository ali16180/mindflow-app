import streamlit as st
from textblob import TextBlob
import pandas as pd
from datetime import datetime
import sqlite3

# -----------------------------------
# PAGE CONFIG
# -----------------------------------

st.set_page_config(
    page_title="MindFlow",
    page_icon="🧠",
    layout="centered"
)

# -----------------------------------
# DATABASE SETUP
# -----------------------------------

conn = sqlite3.connect("mindflow.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    journal TEXT,
    mood_score REAL,
    mood_label TEXT
)
""")

conn.commit()

# -----------------------------------
# HEADER
# -----------------------------------

st.title("🧠 MindFlow")
st.write("Tell me how your day’s been. Let your thoughts flow out for a bit.")

# -----------------------------------
# JOURNAL INPUT
# -----------------------------------

user_input = st.text_area(
    "What’s on your mind today?",
    height=180,
    placeholder="Write whatever comes to mind..."
)

# -----------------------------------
# MOOD ANALYSIS
# -----------------------------------

if st.button("Analyze My Mood"):

    if user_input.strip():

        analysis = TextBlob(user_input)
        score = analysis.sentiment.polarity

        # Mood system
        if score > 0.3:
            mood = {
                "label": "Feeling Good ✨",
                "color": "success",
                "message": "You seem to be in a pretty positive headspace today."
            }

        elif score < -0.3:
            mood = {
                "label": "Emotionally Heavy 😔",
                "color": "error",
                "message": "Looks like today’s been weighing on you a bit."
            }

        else:
            mood = {
                "label": "Balanced 🌿",
                "color": "info",
                "message": "Your mood feels fairly steady today."
            }

        result = f"{mood['message']} (Score: {score:.2f})"

        # Display result
        if mood["color"] == "success":
            st.success(result)

        elif mood["color"] == "error":
            st.error(result)

        else:
            st.info(result)

        # Save to database
        cursor.execute("""
        INSERT INTO journals (
            timestamp,
            journal,
            mood_score,
            mood_label
        )
        VALUES (?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            user_input,
            score,
            mood["label"]
        ))

        conn.commit()

    else:
        st.warning("Write something first before analyzing your mood.")

# -----------------------------------
# LOAD JOURNAL DATA
# -----------------------------------

df = pd.read_sql_query(
    "SELECT * FROM journals ORDER BY id ASC",
    conn
)

# -----------------------------------
# MOOD JOURNEY SECTION
# -----------------------------------

st.divider()
st.subheader("📈 Your Mood Journey")

if not df.empty:

    # Chart
    chart_df = df[["timestamp", "mood_score"]].copy()
    chart_df = chart_df.set_index("timestamp")

    st.line_chart(chart_df)

    # History
    with st.expander("📖 View Journal History"):

        display_df = df.rename(columns={
            "timestamp": "Time",
            "journal": "Journal Entry",
            "mood_score": "Mood Score",
            "mood_label": "Mood"
        })

        st.dataframe(
            display_df,
            use_container_width=True
        )

    # -----------------------------------
    # DELETE SECTION
    # -----------------------------------

    st.divider()

    if "confirm_delete" not in st.session_state:
        st.session_state.confirm_delete = False

    if not st.session_state.confirm_delete:

        if st.button("🗑 Delete All Entries"):
            st.session_state.confirm_delete = True
            st.rerun()

    else:

        st.warning(
            "Are you sure? This will permanently delete your journal history."
        )

        col1, col2 = st.columns(2)

        with col1:
            if st.button("Yes, Delete Everything"):

                cursor.execute("DELETE FROM journals")
                conn.commit()

                st.session_state.confirm_delete = False

                st.success("Your journal history has been cleared.")

                st.rerun()

        with col2:
            if st.button("Cancel"):

                st.session_state.confirm_delete = False
                st.rerun()

else:
    st.write(
        "Your mood history will start showing up here once you add entries."
    )