"""Create a sanitized database for the Windows distribution package.

The runtime package needs the knowledge base, but it must not ship account
cookies, account IDs, API keys, notification webhooks, or private history.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


PRIVATE_TABLES = (
    "ai_conversations",
    "chat_messages",
    "orders",
    "delivery_logs",
    "delivery_finalization_states",
    "data_card_reservations",
    "cards",
    "delivery_rules",
    "publish_logs",
    "risk_control_logs",
    "scheduled_rate_logs",
    "scheduled_red_flower_logs",
    "scheduled_task_logs",
    "scheduled_tasks",
    "default_reply_records",
    "email_verifications",
    "captcha_codes",
    "notification_channels",
    "message_notifications",
    "xy_personal_blacklist",
    "xy_platform_blacklist",
    "xy_message_filter_rules",
    "ai_item_cache",
    "rag_documents",
    "item_info",
    "item_replay",
    "keywords",
    "comment_templates",
    "default_replies",
    "product_materials",
    "ai_reply_settings",
    "ai_config_presets",
    "user_settings",
    "cookie_status",
    "cookies",
)

SENSITIVE_SETTINGS = (
    "auto_comment_api_url",
    "auto_red_flower_interval_seconds",
    "qq_notification_api_url",
    "qq_reply_secret_key",
    "smtp_from",
    "smtp_password",
    "smtp_port",
    "smtp_server",
    "smtp_use_ssl",
    "smtp_use_tls",
    "smtp_user",
    "verification_email_api_url",
)


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def create_sanitized_database(source: Path, target: Path) -> dict[str, int | str]:
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    source_connection = sqlite3.connect(str(source))
    target_connection = sqlite3.connect(str(target))
    try:
        # The backup API takes a consistent snapshot even if the local service
        # is currently holding the source database open.
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA foreign_keys = ON")

        # Export only content fields. Account IDs, timestamps and other
        # account-specific columns never enter the package seed file.
        rag_seed: list[dict[str, object]] = []
        rag_table = "shared_rag_documents" if table_exists(target_connection, "shared_rag_documents") else "rag_documents"
        if table_exists(target_connection, rag_table):
            rows = target_connection.execute(
                f"""
                SELECT source_type, title, content, item_id, tags,
                       valid_until, enabled
                FROM {rag_table}
                ORDER BY id
                """
            ).fetchall()
            for source_type, title, content, item_id, tags, valid_until, enabled in rows:
                if str(content or '').strip():
                    rag_seed.append({
                        "source_type": str(source_type or 'faq'),
                        "title": str(title or ''),
                        "content": str(content).strip(),
                        "item_id": str(item_id or ''),
                        "tags": str(tags or ''),
                        "valid_until": str(valid_until or ''),
                        "enabled": bool(enabled),
                    })

        for table_name in PRIVATE_TABLES:
            if table_exists(target_connection, table_name):
                target_connection.execute(f'DELETE FROM "{table_name}"')

        if table_exists(target_connection, "system_settings"):
            placeholders = ", ".join("?" for _ in SENSITIVE_SETTINGS)
            target_connection.execute(
                f"DELETE FROM system_settings WHERE key IN ({placeholders})",
                SENSITIVE_SETTINGS,
            )

        target_connection.commit()
        target_connection.execute("VACUUM")
        integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity}")

        seed_path = target.parent / "rag_seed.json"
        seed_path.write_text(
            json.dumps(rag_seed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "account_count": 0,
            "rag_count": len(rag_seed),
            "item_count": 0,
        }
    finally:
        source_connection.close()
        target_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()

    summary = create_sanitized_database(args.source, args.target)
    print(
        "sanitized database created: "
        f"accounts={summary['account_count']}, "
        f"rag_documents={summary['rag_count']}, "
        f"items={summary['item_count']}"
    )


if __name__ == "__main__":
    main()
