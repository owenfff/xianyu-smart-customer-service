"""Apply the user-approved Niushoushan rules, preserving a database backup."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent
db = sqlite3.connect(root / 'data/xianyu_data.db')
backup_path = root / 'backups' / ('before-niushoushan-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db')
backup_path.parent.mkdir(exist_ok=True)
with sqlite3.connect(backup_path) as backup:
    db.backup(backup)
rules = json.loads((root / 'rag_seed.json').read_text(encoding='utf-8'))
rule = next(row for row in rules if row['title'] == '牛首山团队票说明（需人工确认）')
with db:
    updated = db.execute('''UPDATE shared_rag_documents SET content=?, updated_at=CURRENT_TIMESTAMP
        WHERE title=?''', (rule['content'], rule['title'])).rowcount
    # Disable conflicting old Niushoushan price/FAQ sources; preserve them for review.
    disabled = db.execute('''UPDATE shared_rag_documents SET enabled=0, updated_at=CURRENT_TIMESTAMP
        WHERE title<>? AND (title LIKE '%牛首山%' OR tags LIKE '%牛首山%')
        AND source_type IN ('price', 'faq') AND COALESCE(enabled,1)=1''', (rule['title'],)).rowcount
    added = 0
    if not db.execute('SELECT 1 FROM shared_rag_documents WHERE title=? AND enabled=1', (rule['title'],)).fetchone():
        db.execute('''INSERT INTO shared_rag_documents(source_type,title,content,tags,enabled)
            VALUES (?,?,?,?,1)''', ('faq', rule['title'], rule['content'], rule['tags']))
        added = 1
print(json.dumps({'updated': updated, 'added': added, 'disabled_old_sources': disabled, 'backup': str(backup_path)}, ensure_ascii=False))
db.close()
