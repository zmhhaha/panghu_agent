import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from game_review_agent.crew import create_game_review_crew
from tools.game_play.browser import GameBrowserSession
from tools.game_play.tools import make_game_tools


class GameReviewCrewTests(unittest.TestCase):
    def test_skill_is_generated_before_play_and_passed_as_context(self):
        with tempfile.TemporaryDirectory() as out_dir:
            session = GameBrowserSession()
            browser_tools = make_game_tools(session, out_dir)
            try:
                with patch(
                    "game_review_agent.crew.Crew",
                    side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
                ):
                    crew = create_game_review_crew(
                        game_url="https://example.com/game",
                        browser_tools=browser_tools,
                        out_dir=out_dir,
                    )
            finally:
                session.close()

            self.assertEqual(len(crew.agents), 4)
            self.assertEqual(len(crew.tasks), 4)

            skill_task, play_task, evaluate_task, report_task = crew.tasks
            self.assertEqual(skill_task.output_file, os.path.join(out_dir, "skill.md"))
            self.assertIn(skill_task, play_task.context)
            self.assertIn(play_task, evaluate_task.context)
            self.assertIn(evaluate_task, report_task.context)
            self.assertIn("不含临时 idx", skill_task.expected_output)
            self.assertFalse(crew.agents[0].memory)
            self.assertFalse(crew.agents[1].memory)
            self.assertIs(crew.agents[0].tools[0]._page, session)
            self.assertIs(crew.agents[1].tools[0]._page, session)


if __name__ == "__main__":
    unittest.main()
