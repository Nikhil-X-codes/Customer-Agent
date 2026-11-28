"""Small deterministic embedding stub for Qdrant integration tests."""

import hashlib
import re


class FakeEmbedder:
    def embed(self, texts):
        for text in texts:
            vector = [0.0] * 384
            for word in re.findall(r"[a-z]+", text.lower()):
                if word in {"what", "is", "the", "for", "a", "how", "small", "standard", "orders"}:
                    continue
                word = {"costs": "cost", "price": "cost",
                        "adjustments": "adjustment",
                        "delivery": "shipping"}.get(word, word)
                index = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % 384
                vector[index] += 1.0
            yield vector
