"""VAPID の鍵を標準出力だけに表示する。"""

from __future__ import annotations

import base64

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "VAPID の公開鍵と秘密鍵を表示します（ファイルには書きません）"

    def handle(self, *args, **options) -> None:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError as exc:
            raise CommandError('鍵を作るには pip install ".[push]" を実行してください') from exc
        key = ec.generate_private_key(ec.SECP256R1())
        public = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        private = key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        for name, value in (("PUBLIC", public), ("PRIVATE", private)):
            encoded = base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
            self.stdout.write(f"HAKONIWA_VAPID_{name}_KEY={encoded}")
