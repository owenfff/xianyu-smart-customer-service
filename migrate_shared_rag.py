"""Migrate XiaoXiao's newest enabled RAG entries to the account-shared library."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent
source_path = root / 'data' / 'xianyu_data.db'
backup_path = root / 'backups' / ('before-shared-rag-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db')
backup_path.parent.mkdir(exist_ok=True)

db = sqlite3.connect(source_path)
with sqlite3.connect(backup_path) as backup:
    db.backup(backup)

with db:
    db.execute('''CREATE TABLE IF NOT EXISTS shared_rag_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL DEFAULT 'faq',
        title TEXT DEFAULT '', content TEXT NOT NULL, item_id TEXT, tags TEXT DEFAULT '',
        valid_until TEXT, enabled INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_shared_rag_scope ON shared_rag_documents(item_id, enabled, updated_at)')
    db.execute('DELETE FROM shared_rag_documents')
    # A repeated title represents later edits; preserve only XiaoXiao's newest active version.
    rows = db.execute('''SELECT source_type,title,content,item_id,tags,valid_until,enabled,created_at,updated_at
        FROM rag_documents r
        WHERE cookie_id='小小' AND COALESCE(enabled,1)=1
          AND id=(SELECT MAX(r2.id) FROM rag_documents r2
                  WHERE r2.cookie_id='小小' AND COALESCE(r2.enabled,1)=1
                    AND COALESCE(r2.title,'')=COALESCE(r.title,''))
        ORDER BY id''').fetchall()
    db.executemany('''INSERT INTO shared_rag_documents
        (source_type,title,content,item_id,tags,valid_until,enabled,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)''', rows)

print(json.dumps({'shared_documents': len(rows), 'source': '小小（同标题保留最新版本）',
                  'backup': str(backup_path)}, ensure_ascii=False))
db.close()
