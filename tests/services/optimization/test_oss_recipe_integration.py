"""Integration tests that resolve TIP-05 policies against the real
``recipes/oss/`` registry. Skipped when the engine cannot load.
"""

from __future__ import annotations

import pytest

try:
    _ENGINE_AVAILABLE = True
except Exception:  # pragma: no cover - the engine should always be available
    _ENGINE_AVAILABLE = False


from tokenpak.services.optimization.route_recipe_policy import (
    DEFAULT_POLICIES,
    RouteClass,
    select_recipes,
)

pytestmark = pytest.mark.skipif(
    not _ENGINE_AVAILABLE, reason="OSS recipe engine unavailable in this build",
)


def test_git_diff_review_resolves_at_least_one_recipe():
    policy = DEFAULT_POLICIES[RouteClass.GIT_DIFF_REVIEW]
    recipes = select_recipes(policy, content_sample="@@ -1 +1 @@\n+ x\n")
    assert recipes, (
        "git_diff_review policy referenced no loadable recipes — "
        "expected cp-git-diff-compression to be in recipes/oss/"
    )
    names = [r.name for r in recipes]
    assert "cp-git-diff-compression" in names


def test_debugging_resolves_log_or_stack_recipe():
    policy = DEFAULT_POLICIES[RouteClass.DEBUGGING]
    recipes = select_recipes(
        policy,
        content_sample="ValueError: nope\n  File \"x.py\", line 1, in foo\n",
    )
    assert recipes
    names = {r.name for r in recipes}
    # At least one of the two referenced recipes is present.
    assert names & {"cp-stack-trace-trimming", "cp-log-output-compression"}


def test_test_failure_resolves_log_or_stack_recipe():
    policy = DEFAULT_POLICIES[RouteClass.TEST_FAILURE]
    recipes = select_recipes(
        policy,
        content_sample="ERROR test_x failed\nAssertionError: 1 != 2\n",
    )
    assert recipes
    names = {r.name for r in recipes}
    assert names & {"cp-log-output-compression", "cp-stack-trace-trimming"}


def test_configuration_inspection_resolves_yaml_recipe():
    policy = DEFAULT_POLICIES[RouteClass.CONFIGURATION_INSPECTION]
    recipes = select_recipes(
        policy,
        content_sample="name: tokenpak\n# comment\nversion: 1.0\n",
    )
    assert recipes
    names = {r.name for r in recipes}
    # cfg-yaml-comment-stripping ships in recipes/oss/.
    assert "cfg-yaml-comment-stripping" in names


def test_documentation_generation_recipes_resolve_or_skip_safely():
    policy = DEFAULT_POLICIES[RouteClass.DOCUMENTATION_GENERATION]
    # md-code-block-compression / md-table-compression may or may not
    # ship today; either way select_recipes must not crash and must
    # only return recipes that exist.
    recipes = select_recipes(
        policy,
        content_sample="# Title\n\n```python\ndef foo():\n    pass\n```\n",
    )
    for r in recipes:
        assert r.name in policy.recipe_names


def test_no_optimize_returns_empty_for_unknown_route():
    policy = DEFAULT_POLICIES[RouteClass.UNKNOWN]
    assert select_recipes(policy, content_sample="") == []
