"""Framework-neutral academic bridge and offline output validation."""
from .validation import validate_research_outputs, validate_workspace
from .schemas import OUTPUT_SCHEMA, REQUIRED_FILES, VALIDATOR_VERSION
from .prompt import build_research_prompt, BUNDLE_REVISION, BUNDLE_MANIFEST, TASK_TEMPLATE, SKILL_PATH

__all__ = ["validate_research_outputs", "validate_workspace", "OUTPUT_SCHEMA", "REQUIRED_FILES", "VALIDATOR_VERSION", "build_research_prompt", "BUNDLE_REVISION", "BUNDLE_MANIFEST", "TASK_TEMPLATE", "SKILL_PATH"]
