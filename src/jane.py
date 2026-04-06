#!/usr/bin/env python3
"""
Jane - עוזרת בית חכמה
הרצה: python3 jane.py
"""

import time
from voice import record_audio, transcribe, speak
from brain import think, execute
from ha_client import test_connection
from memory import process_memory, append_action, rebuild_home_map
from config import HA_URL

def main():
    print("=" * 40)
    print("        ג'ן - עוזרת בית חכמה")
    print("=" * 40)
    
    # בדיקת חיבור ל-HA
    print(f"\n🔌 מתחבר ל-Home Assistant ({HA_URL})...")
    if test_connection():
        print("✅ מחובר בהצלחה!")
    else:
        print("⚠️  לא מצליח להתחבר ל-HA — ממשיך בכל זאת")
    
    # בניית מפת הבית
    print("\n🏠 בונה מפת בית...")
    rebuild_home_map()

    user_name = input("\n👤 שם משתמש (Enter לברירת מחדל): ").strip() or "default"

    print("\n💡 הוראות:")
    print("   לחץ Enter כדי לדבר עם ג'יין")
    print("   הקלד 'יציאה' כדי לצאת")
    print("-" * 40)

    while True:
        cmd = input("\n⏎  לחץ Enter לדיבור (או 'יציאה'): ").strip()
        if cmd in ["יציאה", "exit", "quit"]:
            speak("להתראות!")
            break

        # הקלטה
        audio = record_audio(duration=6)

        # תמלול
        print("🤔 מעבד...")
        text = transcribe(audio)

        if not text:
            print("❌ לא הצלחתי להבין")
            continue

        print(f"👤 אתה: {text}")

        # חשיבה + ביצוע
        result = think(text, user_name=user_name)
        response = execute(result)

        # תשובה קולית
        speak(response)

        # לוג + זיכרון
        action = result.get("action", "speak")
        append_action(user_name, response)

        silent = any(p in text for p in ["אל תזכרי", "אל תזכור", "מצב שקט"])
        if not silent:
            process_memory(user_name, text, response, action)

if __name__ == "__main__":
    main()
