import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import find_terms, classify_verdict, critic_pass, ResearchResult, AppSeed, PageHit

class TestAgentHeuristics(unittest.TestCase):
    def test_find_terms(self):
        text = "This API uses OAuth 2 and requires admin approval."
        terms = {
            "OAuth2": ["oauth 2", "oauth2"],
            "API key": ["api key", "apikey"],
            "Review/admin gate": ["admin approval", "app review"]
        }
        found = find_terms(text, terms)
        self.assertIn("OAuth2", found)
        self.assertIn("Review/admin gate", found)
        self.assertNotIn("API key", found)

    def test_find_terms_exact_word(self):
        # Should not match 'jwt' inside another word if it's alphanumeric and length <= 4
        text = "This is a random text with no auth."
        terms = {"JWT": ["jwt"]}
        self.assertNotIn("JWT", find_terms(text, terms))
        
        text2 = "We use a jwt token."
        self.assertIn("JWT", find_terms(text2, terms))

    def test_critic_pass(self):
        # Create a dummy ResearchResult
        result = ResearchResult(
            id=1, category="CRM", app="TestApp", hint="test.com",
            what_it_does="Tests stuff", auth_methods="Unknown / not verified",
            access="Access path unclear from fetched docs.", surface="moderate/focused",
            mcp="No MCP", verdict="Investigate further", blocker="test",
            confidence="Low", evidence=["http://test.com/docs"],
            source_mode="direct", pages_fetched=1, evidence_terms={}, critic_flags=[]
        )
        flags = critic_pass(result)
        self.assertIn("auth_missing", flags)
        self.assertIn("access_unclear", flags)
        self.assertIn("low_confidence", flags)

if __name__ == "__main__":
    unittest.main()
