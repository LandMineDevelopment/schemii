"""Validate a restored backup exclusively inside the disposable verifier network."""
import json
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from bundle import validate
from schemii.common.metadata.crypto import CredentialCipher, EncryptedCredential
from schemii.common.metadata.database import MetadataMigrator, packaged_migrations
from schemii.common.metadata.secrets import read_encryption_key


def connection():
    # This destination is intentionally not configurable from the backup/env.
    return psycopg.connect(host="database", dbname="schemii_verify", user="restore_app",
                           connect_timeout=5, row_factory=dict_row)


def envelope(row):
    return EncryptedCredential(bytes(row["ciphertext"]), bytes(row["nonce"]), row["key_version"])


def verify():
    root = Path("/backup")
    validate(root)
    cipher = CredentialCipher(read_encryption_key(str(root / "secrets/metadata_encryption_key")))
    probe = json.loads((root / "key-probe.json").read_text())
    probe["ciphertext"] = bytes.fromhex(probe["ciphertext"])
    probe["nonce"] = bytes.fromhex(probe["nonce"])
    if cipher.decrypt("recovery", "key-probe", envelope(probe)) != "schemii-recovery-v1":
        raise ValueError("Recovered key does not match running application's key")
    migrations = packaged_migrations((
        "schemii.common.metadata.migrations", "schemii.schemii.metadata.migrations",
        "schemii.schemoo.metadata.migrations", "schemii.schemer.metadata.migrations",
    ))
    version = MetadataMigrator(connection, migrations).migrate()
    count = 0
    with connection() as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT owner_id,connection_id,ciphertext,nonce,key_version FROM metadata.postgres_connection_credentials")
            for row in cursor:
                cipher.decrypt(row["owner_id"], row["connection_id"], envelope(row))
                count += 1
            cursor.execute("SELECT owner_id,credential_id,provider_id,ciphertext,nonce,key_version FROM metadata.ai_credentials WHERE ciphertext IS NOT NULL")
            for row in cursor:
                recovered = json.loads(cipher.decrypt(row["owner_id"], "ai-credential:" + row["credential_id"], envelope(row)))
                if recovered["provider_id"] != row["provider_id"] or not recovered["credential"]:
                    raise ValueError("Recovered AI credential envelope is inconsistent")
                count += 1
            cursor.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('metadata','schemii','schemoo','schemer') ORDER BY schemaname,tablename")
            tables = cursor.fetchall()
            rows = 0
            for table in tables:
                cursor.execute(sql.SQL("SELECT count(*) AS total FROM {}.{}").format(
                    sql.Identifier(table["schemaname"]), sql.Identifier(table["tablename"])))
                rows += cursor.fetchone()["total"]
    print(json.dumps({"verified": True, "migration_version": version,
                      "tables_read": len(tables), "rows_read": rows,
                      "credentials_decrypted": count, "key_probe_verified": True}))


if __name__ == "__main__":
    try:
        verify()
    except Exception as error:
        # PostgreSQL exception detail may contain row data; show only its class.
        raise SystemExit(f"Isolated restore verification failed ({type(error).__name__}); live database untouched") from None
