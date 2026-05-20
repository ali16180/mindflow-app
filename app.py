import streamlit as st
from textblob import TextBlob
import pandas as pd
from datetime import datetime

# 1. SETUP HALAMAN & DATABASE SEMENTARA (SESSION STATE)
st.set_page_config(page_title="MindFlow", page_icon="🧠")

# Kita pakai session_state untuk menyimpan riwayat jurnal selama aplikasi berjalan
if 'journal_history' not in st.session_state:
    st.session_state.journal_history = []

# 2. TAMPILAN UI (HEADER)
st.title("🧠 MindFlow: AI Mood Journal")
st.write("Ceritain hari ini kamu ngerasa gimana. AI akan menganalisis mood kamu!")

# 3. INPUT DARI USER
user_input = st.text_area("Tulis jurnal kamu di sini (Gunakan Bahasa Inggris untuk hasil terbaik di MVP ini):", height=150)

# 4. TOMBOL ANALISIS & LOGIKA AI
if st.button("Simpan & Analisis Mood"):
    if user_input:
        # Proses Analisis Sentimen menggunakan TextBlob
        analysis = TextBlob(user_input)
        score = analysis.sentiment.polarity  # Hasilnya antara -1.0 (Sangat Sedih/Marah) sampai 1.0 (Sangat Bahagia)
        
        # Menentukan kategori mood berdasarkan skor
        if score > 0.3:
            mood = "Bahagia 😊"
            color = "success"
        elif score < -0.3:
            mood = "Sedih / Stres 😔"
            color = "error"
        else:
            mood = "Netral 😐"
            color = "info"
            
        # Tampilkan hasil ke user
        if color == "success": st.success(f"Mood kamu terdeteksi: {mood} (Skor: {score:.2f})")
        elif color == "error": st.error(f"Mood kamu terdeteksi: {mood} (Skor: {score:.2f}). Jangan lupa istirahat ya!")
        else: st.info(f"Mood kamu terdeteksi: {mood} (Skor: {score:.2f})")
        
        # Simpan data ke history
        st.session_state.journal_history.append({
            "Waktu": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Jurnal": user_input,
            "Skor Mood": score,
            "Kategori": mood
        })
    else:
        st.warning("Tulis sesuatu dulu ya sebelum dianalisis!")

# 5. VISUALISASI DATA (TRACKING MOOD)
st.divider()
st.subheader("📈 Tren Mood Kamu")

if st.session_state.journal_history:
    # Ubah data history jadi Pandas DataFrame biar gampang dibikin grafik
    df = pd.DataFrame(st.session_state.journal_history)
    
    # Tampilkan grafik garis (Line Chart) bawaan Streamlit
    st.line_chart(df.set_index("Waktu")["Skor Mood"])
    
    # Tampilkan tabel riwayat di bawahnya
    with st.expander("Lihat Riwayat Jurnal"):
        st.dataframe(df)
else:
    st.write("Belum ada data. Tulis jurnal pertamamu di atas!")