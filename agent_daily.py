"""
OG Agent — daily heartbeat for a scheduled task.

The app already builds the morning brief on the first visit of the day; this
script exists so the brief (and its Telegram push) goes out even on days the
app isn't opened. On PythonAnywhere (free tier includes ONE daily task):
  Tasks → add:  python3.10 /home/oggainz/oggainz-os/agent_daily.py
Safe to run any number of times — it no-ops after the first run of the day.
"""
import db
import agent

if __name__ == "__main__":
    conn = db.init_db()
    sent = agent.run_daily(conn)
    conn.close()
    print("Morning brief sent." if sent else "Already sent today — nothing to do.")
