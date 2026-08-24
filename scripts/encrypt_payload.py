#!/usr/bin/env python
"""Build an encrypted /wallet/transfer body, for curl/Postman.

    python -m scripts.encrypt_payload payee 300.00
    -> {"payload": "<base64 iv>:<base64 ciphertext>"}
"""

import json
import sys

from app.core.config import settings
from app.core.crypto import encrypt

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python -m scripts.encrypt_payload <recipient_username> <amount>")
    recipient, amount = sys.argv[1], sys.argv[2]
    plaintext = json.dumps({"recipient_username": recipient, "amount": amount})
    print(json.dumps({"payload": encrypt(plaintext, settings.aes_key)}))
