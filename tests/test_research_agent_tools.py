import unittest

from research_agent.crew import create_researcher


class ResearchAgentToolTests(unittest.TestCase):
    def test_researcher_exposes_academic_and_web_search(self):
        tool_names = [tool.name for tool in create_researcher().tools]

        self.assertEqual(
            tool_names,
            ["academic_search", "web_search", "web_fetch", "multi_fetch"],
        )


if __name__ == "__main__":
    unittest.main()
