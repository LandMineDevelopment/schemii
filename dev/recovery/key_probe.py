"""Emit a nonsecret encrypted probe using the running application's key."""
import json
import os
from psycopg.conninfo import conninfo_to_dict

from schemii.common.metadata.crypto import CredentialCipher
from schemii.common.metadata.secrets import read_encryption_key

cipher = CredentialCipher(read_encryption_key(os.environ["SCHEMII_METADATA_ENCRYPTION_KEY_FILE"]))
encrypted = cipher.encrypt("recovery", "key-probe", "schemii-recovery-v1")
identity = conninfo_to_dict(os.environ["SCHEMII_METADATA_DSN"])
print(json.dumps({"ciphertext": encrypted.ciphertext.hex(), "nonce": encrypted.nonce.hex(),
                  "key_version": encrypted.key_version,
                  "database": identity["dbname"], "app_user": identity["user"]}))
