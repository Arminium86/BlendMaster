conn = sqlite3.connect('blend_results.db')
cursor = conn.cursor()

cursor.execute('SELECT * FROM blend_results')
rows = cursor.fetchall()

for row in rows:
    print(row)

conn.close()
