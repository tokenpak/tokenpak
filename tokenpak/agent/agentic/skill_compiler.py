"""Skill extraction and promotion for repeated agent episodes."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tokenpak.agent.macros.engine import MacroEngine, MacroResult

DEFAULT_SKILLS_DIR = Path.home() / ".tokenpak" / "skills"
DEFAULT_SKILL_INDEX = DEFAULT_SKILLS_DIR / "_index.json"

PROMOTION_MIN_SUCCESSFUL_EPISODES = 3
PROMOTION_MIN_SUCCESS_RATE = 0.80
PROMOTION_MIN_TOKEN_SAVINGS = 0.30
RECENT_FAILURE_WINDOW = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:64] or "skill"


def _average(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _token_savings_ratio(avg_tokens_original: float, avg_tokens_skill: float) -> float:
    if avg_tokens_original <= 0:
        return 0.0
    return max(0.0, (avg_tokens_original - avg_tokens_skill) / avg_tokens_original)


def _normalize_tool_sequence(tool_sequence: List[str]) -> List[str]:
    return [tool.strip().lower() for tool in tool_sequence if tool and tool.strip()]


def _normalize_file_targets(file_targets: List[str]) -> List[str]:
    return sorted({target.strip() for target in file_targets if target and target.strip()})


@dataclass
class SkillEpisode:
    """A completed task execution that may contribute to skill extraction."""

    task_type: str
    tool_sequence: List[str]
    file_targets: List[str]
    steps: List[Dict[str, Any]]
    validation: Dict[str, Any] | str
    success: bool
    validation_passed: bool = True
    tokens_original: int = 0
    tokens_skill: int = 0
    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_now_iso)
    outcome: Dict[str, Any] = field(default_factory=dict)

    def normalized_pattern(self) -> Dict[str, Any]:
        return {
            "task_type": self.task_type.strip().lower(),
            "tool_sequence": _normalize_tool_sequence(self.tool_sequence),
            "file_targets": _normalize_file_targets(self.file_targets),
        }

    def pattern_key(self) -> str:
        return json.dumps(self.normalized_pattern(), sort_keys=True)

    @property
    def counted_success(self) -> bool:
        return self.success and self.validation_passed


@dataclass
class ExtractedSkill:
    """A promoted skill derived from repeated successful episodes."""

    skill_id: str
    name: str
    trigger_pattern: Dict[str, Any]
    steps: List[Dict[str, Any]]
    validation: Dict[str, Any] | str
    source_episodes: List[str]
    avg_token_savings: float
    avg_tokens_original: float = 0.0
    avg_tokens_skill: float = 0.0
    success_rate: float = 0.0
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractedSkill":
        return cls(**data)


@dataclass
class PatternStats:
    pattern_key: str
    trigger_pattern: Dict[str, Any]
    episodes: List[SkillEpisode]

    @property
    def successful_episodes(self) -> List[SkillEpisode]:
        return [episode for episode in self.episodes if episode.counted_success]

    @property
    def success_rate(self) -> float:
        if not self.episodes:
            return 0.0
        return len(self.successful_episodes) / len(self.episodes)

    @property
    def avg_tokens_original(self) -> float:
        return _average([float(episode.tokens_original) for episode in self.successful_episodes])

    @property
    def avg_tokens_skill(self) -> float:
        return _average([float(episode.tokens_skill) for episode in self.successful_episodes])

    @property
    def avg_token_savings(self) -> float:
        return _token_savings_ratio(self.avg_tokens_original, self.avg_tokens_skill)

    @property
    def recent_failures(self) -> List[SkillEpisode]:
        recent = sorted(self.episodes, key=lambda episode: episode.timestamp)[
            -RECENT_FAILURE_WINDOW:
        ]
        return [episode for episode in recent if not episode.counted_success]

    @property
    def contradicted_by_recent_failures(self) -> bool:
        return bool(self.recent_failures)


class SkillStore:
    """Persistent storage for extracted skills and their macro registrations."""

    def __init__(
        self,
        skills_dir: Optional[Path | str] = None,
        macro_engine: Optional[MacroEngine] = None,
        index_path: Optional[Path | str] = None,
    ) -> None:
        self.skills_dir = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
        self.index_path = Path(index_path) if index_path else self.skills_dir / "_index.json"
        self.macro_engine = macro_engine or MacroEngine()
        self._index = self._load_index()

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        try:
            raw = json.loads(self.index_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw

    def _persist_index(self) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self._index, indent=2))

    def _skill_path(self, skill_id: str) -> Path:
        return self.skills_dir / f"{skill_id}.json"

    def _build_macro_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert extracted skill steps to MacroStep-compatible format.

        Handles both formats:
        - MacroStep format: {'name': ..., 'cmd': ...}
        - Tool format: {'tool': ..., 'args': ...}
        """
        macro_steps: List[Dict[str, Any]] = []
        for step in steps:
            if "name" in step and "cmd" in step:
                # Already in correct format
                macro_steps.append(step)
            elif "tool" in step:
                # Convert from tool format to macro format
                tool = step.get("tool", "unknown_tool")
                args = step.get("args", {})
                cmd_parts = [tool]
                if isinstance(args, dict):
                    for k, v in args.items():
                        cmd_parts.append(f"--{k} {v}")
                elif isinstance(args, list):
                    cmd_parts.extend(str(a) for a in args)
                else:
                    cmd_parts.append(str(args))
                macro_steps.append(
                    {
                        "name": tool.replace("-", "_").replace(".", "_").lower()[:32],
                        "cmd": " ".join(cmd_parts),
                        "label": step.get("label", tool),
                        "timeout": step.get("timeout", 60),
                    }
                )
            else:
                # Fallback: treat entire step as a command
                macro_steps.append(
                    {
                        "name": f"step_{len(macro_steps)}",
                        "cmd": json.dumps(step) if isinstance(step, dict) else str(step),
                        "label": "Auto-converted step",
                        "timeout": 60,
                    }
                )
        return macro_steps

    def register_with_macro_engine(self, skill: ExtractedSkill, overwrite: bool = True) -> Path:
        """Register extracted skill with macro engine, converting step format as needed."""
        macro_steps = self._build_macro_steps(skill.steps)
        return self.macro_engine.create(
            name=skill.skill_id,
            steps=macro_steps,
            description=f"Extracted skill for {skill.name}",
            continue_on_error=False,
            overwrite=overwrite,
        )

    def save(self, skill: ExtractedSkill, overwrite: bool = True) -> Path:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        path = self._skill_path(skill.skill_id)
        if path.exists() and not overwrite:
            raise ValueError(f"Skill '{skill.skill_id}' already exists at {path}")
        path.write_text(json.dumps(skill.to_dict(), indent=2))
        self._index[skill.skill_id] = {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "trigger_pattern": skill.trigger_pattern,
            "created_at": skill.created_at,
            "avg_token_savings": skill.avg_token_savings,
        }
        self._persist_index()
        self.register_with_macro_engine(skill, overwrite=overwrite)
        return path

    def get(self, skill_id: str) -> Optional[ExtractedSkill]:
        path = self._skill_path(skill_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return ExtractedSkill.from_dict(data)

    def list_all(self) -> List[ExtractedSkill]:
        skills: List[ExtractedSkill] = []
        for skill_path in sorted(self.skills_dir.glob("*.json")):
            if skill_path.name == self.index_path.name:
                continue
            try:
                skills.append(ExtractedSkill.from_dict(json.loads(skill_path.read_text())))
            except (json.JSONDecodeError, OSError, TypeError):
                continue
        return skills

    def execute(
        self, skill_id: str, variables: Optional[Dict[str, Any]] = None, dry_run: bool = False
    ) -> MacroResult:
        skill = self.get(skill_id)
        if skill is None:
            raise FileNotFoundError(f"Skill '{skill_id}' not found")
        if not self.macro_engine.exists(skill.skill_id):
            self.register_with_macro_engine(skill)
        return self.macro_engine.run(skill.skill_id, variables=variables, dry_run=dry_run)


class SkillCompiler:
    """Detect repeated successful episodes and promote them into reusable skills."""

    def __init__(
        self,
        store: Optional[SkillStore] = None,
        recent_failure_window: int = RECENT_FAILURE_WINDOW,
    ) -> None:
        self.store = store or SkillStore()
        self.recent_failure_window = recent_failure_window
        self._episodes: List[SkillEpisode] = []

    def record_episode(self, episode: SkillEpisode) -> Optional[ExtractedSkill]:
        self._episodes.append(episode)
        return self.maybe_promote(episode.pattern_key())

    def pattern_stats(self, pattern_key: Optional[str] = None) -> Dict[str, PatternStats]:
        grouped: Dict[str, List[SkillEpisode]] = {}
        for episode in self._episodes:
            grouped.setdefault(episode.pattern_key(), []).append(episode)

        stats: Dict[str, PatternStats] = {}
        for key, episodes in grouped.items():
            if pattern_key is not None and key != pattern_key:
                continue
            stats[key] = PatternStats(
                pattern_key=key,
                trigger_pattern=episodes[0].normalized_pattern(),
                episodes=episodes,
            )
        return stats

    def detect_repeated_patterns(self) -> List[PatternStats]:
        return [
            stats
            for stats in self.pattern_stats().values()
            if len(stats.episodes) >= PROMOTION_MIN_SUCCESSFUL_EPISODES
        ]

    def should_promote(self, stats: PatternStats) -> bool:
        if len(stats.successful_episodes) < PROMOTION_MIN_SUCCESSFUL_EPISODES:
            return False
        if stats.success_rate <= PROMOTION_MIN_SUCCESS_RATE:
            return False
        if stats.avg_token_savings <= PROMOTION_MIN_TOKEN_SAVINGS:
            return False
        recent = sorted(stats.episodes, key=lambda episode: episode.timestamp)[
            -self.recent_failure_window :
        ]
        if any(not episode.counted_success for episode in recent):
            return False
        return True

    def maybe_promote(self, pattern_key: str) -> Optional[ExtractedSkill]:
        stats = self.pattern_stats(pattern_key).get(pattern_key)
        if stats is None or not self.should_promote(stats):
            return None

        candidate = self.compile_skill(stats)
        existing = self.store.get(candidate.skill_id)
        if existing is not None:
            return existing
        self.store.save(candidate)
        return candidate

    def compile_skill(self, stats: PatternStats) -> ExtractedSkill:
        seed = stats.successful_episodes[-1]
        task_type = stats.trigger_pattern["task_type"]
        primary_file = (
            Path(stats.trigger_pattern["file_targets"][0]).stem
            if stats.trigger_pattern["file_targets"]
            else task_type
        )
        skill_id = _slugify(f"{task_type}-{primary_file}")
        return ExtractedSkill(
            skill_id=skill_id,
            name=f"{task_type.replace('_', ' ')} via {' -> '.join(stats.trigger_pattern['tool_sequence'])}",
            trigger_pattern=stats.trigger_pattern,
            steps=seed.steps,
            validation={
                "check": seed.validation,
                "success_rate": round(stats.success_rate, 4),
                "avg_tokens_original": round(stats.avg_tokens_original, 2),
                "avg_tokens_skill": round(stats.avg_tokens_skill, 2),
            },
            source_episodes=[episode.episode_id for episode in stats.successful_episodes],
            avg_token_savings=round(stats.avg_token_savings, 4),
            avg_tokens_original=round(stats.avg_tokens_original, 2),
            avg_tokens_skill=round(stats.avg_tokens_skill, 2),
            success_rate=round(stats.success_rate, 4),
        )
