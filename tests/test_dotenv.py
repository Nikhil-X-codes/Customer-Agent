import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support_agent.dotenv import load_dotenv


class DotEnvTests(unittest.TestCase):
    def test_loads_key_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("# local config\nAI_MODE=groq\nGROQ_API_KEY='example-key'\nBAD KEY=ignored\n")
            with patch.dict(os.environ, {"AI_MODE": "off"}, clear=True):
                load_dotenv(path)
                self.assertEqual(os.environ["AI_MODE"], "off")
                self.assertEqual(os.environ["GROQ_API_KEY"], "example-key")
                self.assertNotIn("BAD KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
