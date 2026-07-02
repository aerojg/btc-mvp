import sqlite3

db_path = 'btc_onchain.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 기존 컬럼 확인
cursor.execute("PRAGMA table_info(daily_metrics)")
existing_cols = [row[1] for row in cursor.fetchall()]
print(f"기존 컬럼: {existing_cols}")

# 새 컬럼 추가
try:
    cursor.execute("ALTER TABLE daily_metrics ADD COLUMN fear_greed_index REAL")
    print("✓ fear_greed_index 추가 완료")
except sqlite3.OperationalError:
    print("fear_greed_index: 이미 있음")

try:
    cursor.execute("ALTER TABLE daily_metrics ADD COLUMN google_trend_score REAL")
    print("✓ google_trend_score 추가 완료")
except sqlite3.OperationalError:
    print("google_trend_score: 이미 있음")

try:
    cursor.execute("ALTER TABLE daily_metrics ADD COLUMN reddit_mentions INTEGER")
    print("✓ reddit_mentions 추가 완료")
except sqlite3.OperationalError:
    print("reddit_mentions: 이미 있음")

conn.commit()

# 확인: 수정 후 컬럼
cursor.execute("PRAGMA table_info(daily_metrics)")
new_cols = [row[1] for row in cursor.fetchall()]
print(f"\n수정 후 컬럼: {new_cols}")

conn.close()
print("\n✅ DB 스키마 확장 완료!")